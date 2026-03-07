import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from typing import Callable, cast
from zoneinfo import ZoneInfo

import requests

import partita_bot.config as config
from partita_bot.admin_operations import (
    ADMIN_OPERATION_PREFIX,
    ADMIN_OPERATIONS,
    CLEANUP_USERS,
    DELETE_SENT_LAST_HOURS,
    NOTIFY_ALL_USERS,
    NOTIFY_SINGLE_USER,
    RECHECK_BLOCKED_USERS,
)
from partita_bot.bot import run_bot
from partita_bot.bot_manager import get_bot
from partita_bot.event_fetcher import FETCH_FAILURE, EventFetcher
from partita_bot.notifications import process_notifications
from partita_bot.scheduler import create_scheduler
from partita_bot.storage import Database, is_user_blocked_error

logging_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging_level,
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _log_converter(seconds: float | None) -> time.struct_time:
    return config.timezone_converter(seconds).timetuple()


logging.Formatter.converter = staticmethod(_log_converter)
logger = logging.getLogger(__name__)


RECHECK_OPERATION = RECHECK_BLOCKED_USERS
LEGACY_CLEANUP_OPERATION = CLEANUP_USERS
DELETE_SENT_OPERATION = DELETE_SENT_LAST_HOURS
NOTIFY_ALL_OPERATION = NOTIFY_ALL_USERS
NOTIFY_SINGLE_OPERATION = NOTIFY_SINGLE_USER


async def process_admin_operation(
    bot_instance,
    operation: str,
    operation_id: int,
    db: Database,
    params: list[str] | None = None,
    is_legacy: bool = False,
) -> None:
    if params is None:
        parts = operation.split(":")
        op_type = parts[0]
        params = parts[1:] if len(parts) > 1 else []
    else:
        op_type = operation

    if op_type in {RECHECK_OPERATION, LEGACY_CLEANUP_OPERATION}:
        logger.info("Running user cleanup operation")
        try:
            results = await db.recheck_blocked_users(bot_instance)
            logger.info(
                "Recheck summary -- checked: %s, unblocked: %s, still blocked: %s",
                results["checked"],
                results["unblocked"],
                results["still_blocked"],
            )
            if results["errors"]:
                logger.warning("Recheck errors: %s", ", ".join(results["errors"]))
        except Exception as admin_error:
            logger.error(f"Error during admin operation: {str(admin_error)}")
        finally:
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
    elif op_type == DELETE_SENT_OPERATION:
        if not params:
            logger.error("DELETE_SENT_LAST_HOURS operation missing telegram_id parameter")
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
            return
        try:
            telegram_id = int(params[0])
            hours = int(params[1]) if len(params) > 1 else 1
        except ValueError:
            logger.error("Invalid parameters for DELETE_SENT_LAST_HOURS: %s", params)
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
            return

        logger.info("Deleting sent messages for user %s within last %s hours", telegram_id, hours)
        try:
            results = await db.delete_sent_messages_for_user_within_hours(
                bot_instance, telegram_id, hours
            )
            logger.info(
                "Delete sent messages summary for user %s: %s succeeded, %s failed, "
                "%s total attempted",
                telegram_id,
                results["success_count"],
                results["error_count"],
                results["total_attempted"],
            )
            if results["errors"]:
                logger.warning("Delete errors: %s", "; ".join(results["errors"]))
        except Exception as admin_error:
            logger.error(f"Error during delete sent messages operation: {str(admin_error)}")
        finally:
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
    elif op_type == NOTIFY_ALL_OPERATION:
        logger.info("Processing NOTIFY_ALL operation")
        fetcher = EventFetcher(db)
        local_time = datetime.now(tz=ZoneInfo("UTC")).astimezone(config.TIMEZONE_INFO)
        try:
            summary = process_notifications(
                users=db.get_all_users(),
                db=db,
                fetcher=fetcher,
                queue_message=db.queue_message,
                local_time=local_time,
                mark_manual=True,
            )
            logger.info(
                "NOTIFY_ALL summary: sent=%s, no_events=%s, already_notified=%s, fetch_errors=%s",
                summary["notifications_sent"],
                summary["no_events"],
                summary["already_notified"],
                summary["fetch_errors"],
            )
        except Exception as admin_error:
            logger.error(f"Error during NOTIFY_ALL operation: {str(admin_error)}")
        finally:
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
    elif op_type == NOTIFY_SINGLE_OPERATION:
        if not params:
            logger.error("NOTIFY_SINGLE operation missing user_id parameter")
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
            return
        try:
            user_id = int(params[0])
        except ValueError:
            logger.error("Invalid user_id for NOTIFY_SINGLE: %s", params[0])
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
            return

        logger.info("Processing NOTIFY_SINGLE operation for user %s", user_id)
        user = db.get_user(user_id)
        if not user:
            logger.error("User %s not found for NOTIFY_SINGLE", user_id)
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
            return

        if not db.can_send_manual_notification(user_id):
            logger.warning("User %s is on cooldown, skipping NOTIFY_SINGLE", user_id)
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
            return

        cities = db.get_user_cities(user_id)
        if not cities:
            logger.info("User %s has no cities configured, skipping NOTIFY_SINGLE", user_id)
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
            return

        fetcher = EventFetcher(db)
        local_time = datetime.now(tz=ZoneInfo("UTC")).astimezone(config.TIMEZONE_INFO)
        messages_queued = 0
        failures = 0

        try:
            for city in cities:
                message = fetcher.fetch_event_message(city, local_time.date())
                if message == FETCH_FAILURE:
                    failures += 1
                    continue
                if message:
                    if db.queue_message(user_id, message):
                        messages_queued += 1
                    else:
                        failures += 1

            if messages_queued > 0:
                db.update_last_notification(user_id, is_manual=True)

            logger.info(
                "NOTIFY_SINGLE summary for user %s: queued=%s, failures=%s",
                user_id,
                messages_queued,
                failures,
            )
        except Exception as admin_error:
            logger.error(f"Error during NOTIFY_SINGLE operation: {str(admin_error)}")
        finally:
            if is_legacy:
                db.mark_message_sent(operation_id)
            else:
                db.mark_admin_operation_processed(operation_id)
    else:
        logger.warning(
            "Unknown admin operation: %s (expected one of: %s)",
            op_type,
            ", ".join(ADMIN_OPERATIONS),
        )
        if is_legacy:
            db.mark_message_sent(operation_id)
        else:
            db.mark_admin_operation_processed(operation_id)


def process_queued_message(
    bot_instance,
    db: Database,
    message,
    loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
    sleep_fn: Callable[[float], None] | None = None,
) -> None:
    if message.telegram_id == 0 and message.message.startswith(ADMIN_OPERATION_PREFIX):
        admin_op = message.message.replace(ADMIN_OPERATION_PREFIX, "").strip()
        logger.info("Processing admin operation: %s", admin_op)

        loop = loop_factory()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                process_admin_operation(bot_instance, admin_op, message.id, db, is_legacy=True)
            )
        finally:
            loop.close()
        return

    logger.info("Processing queued message %s for user %s", message.id, message.telegram_id)
    result = bot_instance.send_message_sync(chat_id=message.telegram_id, text=message.message)
    if isinstance(result, tuple) and len(result) >= 2:
        success = result[0]
        error = result[1]
        message_id = result[2] if len(result) > 2 else None
    else:
        success = bool(result)
        error = None
        message_id = None
    if success:
        db.mark_message_sent(message.id, sent_message_id=message_id)
        logger.info(
            "Successfully sent message %s to user %s (msg_id: %s)",
            message.id,
            message.telegram_id,
            message_id,
        )
    elif is_user_blocked_error(error):
        logger.warning(
            "User %s blocked bot, flagging and marking message %s as sent",
            message.telegram_id,
            message.id,
        )
        db.mark_user_blocked(message.telegram_id)
        db.mark_message_sent(message.id)
    else:
        logger.warning(
            "Failed to send message %s to user %s",
            message.id,
            message.telegram_id,
        )

    if sleep_fn is not None:
        sleep_fn(1.0)


def check_telegram_token_in_use(token):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 409:  # Conflict
            logger.warning("Telegram token is already in use by another process.")
            return True
        logger.info("Telegram token is not in use by other processes.")
        return False
    except Exception as e:
        logger.error(f"Error checking Telegram token: {e}")
        return False


if __name__ == "__main__":
    logger.info(f"Starting bot process (PID: {os.getpid()})")

    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        logger.critical("TELEGRAM_BOT_TOKEN is not configured. Aborting startup.")
        sys.exit(1)
    retries = 3
    token_in_use = False

    for i in range(retries):
        if check_telegram_token_in_use(token):
            token_in_use = True
            logger.warning(
                f"Attempt {i + 1}/{retries}: Telegram token in use, waiting 5 seconds..."
            )
            time.sleep(5)
        else:
            token_in_use = False
            break

    if token_in_use:
        logger.critical("Telegram token is in use by another process. Cannot start bot.")
        logger.critical(
            "Check for other running bot instances and stop them before starting this one."
        )
        sys.exit(1)

    bot_instance = get_bot(cast(str, token))

    startup_db = Database()
    deleted_count = startup_db.delete_pending_messages_older_than(hours=24)
    if deleted_count > 0:
        logger.info(f"Purged {deleted_count} old pending messages on startup")
    startup_db.close()

    logger.info("Starting scheduler")
    scheduler = create_scheduler()
    scheduler.start()

    import threading

    def process_admin_queue():
        db = Database()
        logger.info("Starting admin queue processing thread")

        while True:
            try:
                operations = db.get_pending_admin_operations(limit=10)
                for operation in operations:
                    try:
                        op_str = str(operation.operation)
                        logger.info("Processing admin operation: %s", op_str)
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            payload_str = str(operation.payload) if operation.payload else ""
                            params = payload_str.split(":") if payload_str else []
                            loop.run_until_complete(
                                process_admin_operation(
                                    bot_instance,
                                    op_str,
                                    int(operation.id),
                                    db,
                                    params=params,
                                    is_legacy=False,
                                )
                            )
                        finally:
                            loop.close()
                    except Exception as e:
                        logger.error(f"Error processing admin operation {operation.id}: {str(e)}")

                if not operations:
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Error in admin queue processing: {str(e)}")
                time.sleep(5)

    def process_message_queue():
        db = Database()
        logger.info("Starting message queue processing thread")

        while True:
            try:
                messages = db.get_pending_messages(limit=10)
                for message in messages:
                    try:
                        process_queued_message(bot_instance, db, message, sleep_fn=time.sleep)
                    except Exception as e:
                        logger.error(f"Error processing message {message.id}: {str(e)}")

                if not messages:
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Error in message queue processing: {str(e)}")
                time.sleep(5)

    admin_queue_thread = threading.Thread(target=process_admin_queue)
    admin_queue_thread.daemon = True
    admin_queue_thread.start()

    queue_thread = threading.Thread(target=process_message_queue)
    queue_thread.daemon = True
    queue_thread.start()
    logger.info("Starting bot polling")
    run_bot()
