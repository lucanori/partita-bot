import asyncio
import logging

import nest_asyncio
from telegram.error import TelegramError
from telegram.ext import Application

_NEST_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_NEST_LOOP)
nest_asyncio.apply()

logger = logging.getLogger(__name__)


class Bot:
    def __init__(self, token):
        if not token:
            raise ValueError("Bot token cannot be empty")
        self.app = Application.builder().token(token).build()
        self.bot = self.app.bot
        self._loop = None
        logger.debug("Bot initialized with token")

    def _get_event_loop(self):
        if self._loop is None or self._loop.is_closed():
            logger.debug("Creating new event loop")
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    async def _send_message_async(self, chat_id: int, text: str) -> tuple[bool, str | None]:
        try:
            await self.bot.send_message(chat_id=chat_id, text=text)
            return True, None
        except TelegramError as e:
            logger.error(f"Telegram error sending message to {chat_id}: {str(e)}")
            return False, str(e)
        except Exception as e:
            logger.error(f"Unexpected error sending message to {chat_id}: {str(e)}")
            return False, str(e)

    def send_message_sync(self, chat_id: int, text: str) -> tuple[bool, str | None]:
        loop = self._get_event_loop()

        try:
            success, error = loop.run_until_complete(self._send_message_async(chat_id, text))
            if not success:
                logger.warning(f"Failed to send message to {chat_id}: {error}")
            return success, error
        except RuntimeError as e:
            logger.error(f"Runtime error in event loop: {str(e)}")
            self._loop = None
            loop = self._get_event_loop()

            try:
                success, error = loop.run_until_complete(self._send_message_async(chat_id, text))
                if not success:
                    logger.error(f"Failed to send message after loop reset: {error}")
                return success, error
            except Exception as e:
                logger.error(f"Fatal error sending message to {chat_id}: {str(e)}")
                return False, str(e)
