import logging
from typing import cast

import partita_bot.config as config
from partita_bot.admin import app as application
from partita_bot.bot_manager import get_bot, is_bot_initialized

logger = logging.getLogger(__name__)

# Only initialize if not already done
# This ensures WSGI server will have a proper bot instance if needed
if not is_bot_initialized():
    logger.info("Initializing bot in WSGI app (bot not initialized elsewhere)")
    # config.TELEGRAM_BOT_TOKEN may be None; cast to str for get_bot
    get_bot(cast(str, config.TELEGRAM_BOT_TOKEN))
else:
    logger.info("Bot already initialized, not reinitializing in WSGI app")

if __name__ == "__main__":
    application.run()
