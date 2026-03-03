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
NOTIFICATION_START_HOUR = 8
NOTIFICATION_END_HOUR = 10

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
