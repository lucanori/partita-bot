import logging

from partita_bot.admin import app as application

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    application.run()
