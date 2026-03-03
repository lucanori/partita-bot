#!/usr/bin/env python3

"""
Standalone entry point for running the bot.
This separates the bot process from the WSGI server.
"""

import asyncio
import logging
import os
import sys
import time
from typing import Callable, cast

import requests

import partita_bot.config as config
from partita_bot.bot import run_bot
from partita_bot.bot_manager import get_bot
from partita_bot.scheduler import create_scheduler
from partita_bot.storage import Database

# Configure logging
logging_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging_level
)
logger = logging.getLogger(__name__)


async def process_admin_operation(
    bot_instance, operation: str, message_id: int, db: Database
) -> None:
    """Process admin operations that require async functions"""
    if operation == "CLEANUP_USERS":
        logger.info("Running user cleanup operation")
        try:
            results = await db.remove_blocked_users(bot_instance)
            logger.info(
                "Cleanup results: Removed %s of %s users",
                results["removed_users"],
                results["total_users"],
            )
            if results["errors"]:
                logger.warning(f"Cleanup errors: {', '.join(results['errors'])}")
        except Exception as admin_error:
            logger.error(f"Error during admin operation: {str(admin_error)}")

        # Mark the admin message as processed even if the operation failed
        db.mark_message_sent(message_id)


def process_queued_message(
    bot_instance,
    db: Database,
    message,
    loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
) -> None:
    if message.telegram_id == 0 and message.message.startswith("ADMIN_OPERATION:"):
        admin_op = message.message.replace("ADMIN_OPERATION:", "").strip()
        logger.info("Processing admin operation: %s", admin_op)

        loop = loop_factory()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(process_admin_operation(bot_instance, admin_op, message.id, db))
        finally:
            loop.close()
        return

    logger.info("Processing queued message %s for user %s", message.id, message.telegram_id)
    success = bot_instance.send_message_sync(chat_id=message.telegram_id, text=message.message)
    if success:
        db.mark_message_sent(message.id)
        logger.info(
            "Successfully sent message %s to user %s",
            message.id,
            message.telegram_id,
        )
    else:
        logger.warning(
            "Failed to send message %s to user %s",
            message.id,
            message.telegram_id,
        )


def check_telegram_token_in_use(token):
    """Check if the token is already being used by another bot instance"""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        # First attempt might fail if another process is using the token
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

    # Check if the token is already in use
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

    # Initialize the bot
    bot_instance = get_bot(cast(str, token))

    # Initialize and start the scheduler
    logger.info("Starting scheduler")
    scheduler = create_scheduler()
    scheduler.start()

    # Start a thread to process queued messages
    import threading

    def process_message_queue():
        db = Database()
        logger.info("Starting message queue processing thread")

        while True:
            try:
                messages = db.get_pending_messages(limit=10)
                for message in messages:
                    try:
                        process_queued_message(bot_instance, db, message)
                    except Exception as e:
                        logger.error(f"Error processing message {message.id}: {str(e)}")

                # Sleep for a short time if no messages
                if not messages:
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Error in message queue processing: {str(e)}")
                time.sleep(5)  # Back off on error

    # Start the message queue processor thread
    queue_thread = threading.Thread(target=process_message_queue)
    queue_thread.daemon = True
    queue_thread.start()

    # Start the bot polling
    logger.info("Starting bot polling")
    run_bot()
