import asyncio
import logging
import os
import sys
import time
from typing import Callable, cast

import requests

import partita_bot.config as config
from partita_bot.admin_operations import (
    ADMIN_OPERATION_PREFIX,
    CLEANUP_USERS,
    DELETE_SENT_LAST_HOURS,
    RECHECK_BLOCKED_USERS,
)
from partita_bot.bot import run_bot
from partita_bot.bot_manager import get_bot
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


async def process_admin_operation(
    bot_instance, operation: str, message_id: int, db: Database
) -> None:

    parts = operation.split(":")
    op_type = parts[0]
    params = parts[1:] if len(parts) > 1 else []

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
            db.mark_message_sent(message_id)
    elif op_type == DELETE_SENT_OPERATION:
        if not params:
            logger.error("DELETE_SENT_LAST_HOURS operation missing telegram_id parameter")
            db.mark_message_sent(message_id)
            return
        try:
            telegram_id = int(params[0])
            hours = int(params[1]) if len(params) > 1 else 1
        except ValueError:
            logger.error("Invalid parameters for DELETE_SENT_LAST_HOURS: %s", params)
            db.mark_message_sent(message_id)
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
            db.mark_message_sent(message_id)
    else:
        logger.warning("Unknown admin operation: %s", op_type)
        db.mark_message_sent(message_id)


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
            loop.run_until_complete(process_admin_operation(bot_instance, admin_op, message.id, db))
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

    queue_thread = threading.Thread(target=process_message_queue)
    queue_thread.daemon = True
    queue_thread.start()
    logger.info("Starting bot polling")
    run_bot()
