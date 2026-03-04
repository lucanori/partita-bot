import logging
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

SKIP_DOTENV = os.getenv("PARTITA_SKIP_DOTENV", "false").lower()
if SKIP_DOTENV not in {"1", "true", "yes"}:
    load_dotenv()

# Application settings
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
DEFAULT_TIMEZONE = "Europe/Rome"
TIMEZONE = os.getenv("TIMEZONE", DEFAULT_TIMEZONE)

DEFAULT_START_HOUR = 8
DEFAULT_END_HOUR = 20


def _parse_notification_hours() -> tuple[int, int]:
    start_str = os.getenv("NOTIFICATION_START_HOUR")
    end_str = os.getenv("NOTIFICATION_END_HOUR")

    try:
        start = int(start_str) if start_str is not None else DEFAULT_START_HOUR
        end = int(end_str) if end_str is not None else DEFAULT_END_HOUR
    except (ValueError, TypeError):
        logger.warning(
            f"Invalid NOTIFICATION_START_HOUR/NOTIFICATION_END_HOUR. "
            f"Falling back to defaults ({DEFAULT_START_HOUR}, {DEFAULT_END_HOUR})"
        )
        return DEFAULT_START_HOUR, DEFAULT_END_HOUR

    if not (0 <= start <= 23 and 0 <= end <= 23):
        logger.warning(
            f"NOTIFICATION_START_HOUR/NOTIFICATION_END_HOUR out of range (0-23). "
            f"Falling back to defaults ({DEFAULT_START_HOUR}, {DEFAULT_END_HOUR})"
        )
        return DEFAULT_START_HOUR, DEFAULT_END_HOUR

    if start >= end:
        logger.warning(
            f"NOTIFICATION_START_HOUR ({start}) >= NOTIFICATION_END_HOUR ({end}). "
            f"Falling back to defaults ({DEFAULT_START_HOUR}, {DEFAULT_END_HOUR})"
        )
        return DEFAULT_START_HOUR, DEFAULT_END_HOUR

    return start, end


NOTIFICATION_START_HOUR, NOTIFICATION_END_HOUR = _parse_notification_hours()

try:
    TIMEZONE_INFO = ZoneInfo(TIMEZONE)
except ZoneInfoNotFoundError:
    logger.warning(f"Invalid timezone: {TIMEZONE}. Falling back to {DEFAULT_TIMEZONE}")
    TIMEZONE = DEFAULT_TIMEZONE
    TIMEZONE_INFO = ZoneInfo(TIMEZONE)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is not set in environment variables")

EXA_API_KEY = os.getenv("EXA_API_KEY")
if not EXA_API_KEY:
    logger.error("EXA_API_KEY is not set in environment variables")

# Admin interface settings
ADMIN_PORT = int(os.getenv("ADMIN_PORT", "5000"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or os.urandom(24)

# Bot language configuration (global, not per-user)
BOT_LANGUAGE = os.getenv("BOT_LANGUAGE", "English")
