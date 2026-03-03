import logging
import os
import threading
from typing import Optional

from partita_bot.custom_bot import Bot

logger = logging.getLogger(__name__)

# Global instance storage - shared across all processes
_bot_instance: Optional[Bot] = None
_initialized = False
_main_thread_id = threading.get_ident()
_process_id = os.getpid()


def get_bot(token: str) -> Bot:
    """Get or create the bot instance"""
    global _bot_instance, _initialized, _main_thread_id, _process_id

    current_thread = threading.get_ident()
    current_process = os.getpid()

    if _bot_instance is None:
        logger.info(
            f"Creating new bot instance (process: {current_process}, thread: {current_thread})"
        )
        _bot_instance = Bot(token)
        _initialized = True
        _main_thread_id = current_thread
        _process_id = current_process
    else:
        logger.debug(f"Reusing existing bot instance from process {_process_id}")

    # Record whether this is the original process that created the bot
    is_original = current_process == _process_id
    logger.debug(f"Using bot instance: original process? {is_original}")

    return _bot_instance


def is_bot_initialized() -> bool:
    """Check if the bot has already been initialized"""
    return _initialized


def get_owner_info() -> dict:
    """Get information about which process owns the bot instance"""
    return {"process_id": _process_id, "thread_id": _main_thread_id, "initialized": _initialized}
