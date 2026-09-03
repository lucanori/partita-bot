import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any

import nest_asyncio
from telegram.constants import MessageLimit
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import Application

from partita_bot.rich_text import build_rich_html

_NEST_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_NEST_LOOP)
nest_asyncio.apply()

logger = logging.getLogger(__name__)

MAX_SEND_TEXT_LENGTH = MessageLimit.MAX_TEXT_LENGTH
MAX_RICH_TEXT_LENGTH = 32768

STATUS_SENT = "sent"
STATUS_BLOCKED = "blocked"
STATUS_PERMANENT = "permanent"
STATUS_TRANSIENT = "transient"


@dataclass(slots=True)
class DeliveryResult:
    status: str
    error: str | None = None
    message_id: int | None = None
    retry_after: float | None = None

    SENT = STATUS_SENT
    BLOCKED = STATUS_BLOCKED
    PERMANENT = STATUS_PERMANENT
    TRANSIENT = STATUS_TRANSIENT

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SENT

    @property
    def blocked(self) -> bool:
        return self.status == STATUS_BLOCKED

    @property
    def is_retryable(self) -> bool:
        return self.status == STATUS_TRANSIENT

    @property
    def is_terminal(self) -> bool:
        return not self.is_retryable

    def __iter__(self) -> Any:
        yield self.ok
        yield self.error
        yield self.message_id


def _extract_message_id(result: Any) -> int | None:
    if isinstance(result, dict):
        message_id = result.get("message_id")
    else:
        message_id = getattr(result, "message_id", None)
    if isinstance(message_id, int) and not isinstance(message_id, bool):
        return message_id
    return None


def _retry_after_seconds(value: Any) -> float | None:
    try:
        seconds = float(value.total_seconds()) if hasattr(value, "total_seconds") else float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


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

    async def _send_message_async(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
        entities: list[Any] | None = None,
        link_preview_options: Any = None,
    ) -> DeliveryResult:
        text_length = len(text)
        if text_length > MAX_RICH_TEXT_LENGTH:
            logger.error(
                "Refusing to send message to %s: %s code points exceed the %s code point "
                "rich message limit",
                chat_id,
                text_length,
                MAX_RICH_TEXT_LENGTH,
            )
            return DeliveryResult(
                DeliveryResult.PERMANENT,
                error=(
                    f"message length {text_length} exceeds the rich message limit "
                    f"of {MAX_RICH_TEXT_LENGTH}"
                ),
            )
        if text_length <= MAX_SEND_TEXT_LENGTH:
            return await self._send_short_message(
                chat_id, text, parse_mode, entities, link_preview_options
            )
        return await self._send_rich_message(chat_id, text, entities, link_preview_options)

    async def _send_short_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None,
        entities: list[Any] | None,
        link_preview_options: Any,
    ) -> DeliveryResult:
        try:
            kwargs: dict[str, Any] = {"chat_id": chat_id, "text": text}
            if entities:
                kwargs["entities"] = entities
            elif parse_mode:
                kwargs["parse_mode"] = parse_mode
            if link_preview_options is not None:
                kwargs["link_preview_options"] = link_preview_options
            message = await self.bot.send_message(**kwargs)
            return DeliveryResult(DeliveryResult.SENT, message_id=message.message_id)
        except Forbidden as e:
            logger.warning(f"User {chat_id} has blocked the bot: {str(e)}")
            return DeliveryResult(DeliveryResult.BLOCKED, error=str(e))
        except RetryAfter as e:
            logger.warning(f"Flood control while sending message to {chat_id}: {str(e)}")
            return DeliveryResult(
                DeliveryResult.TRANSIENT,
                error=str(e),
                retry_after=_retry_after_seconds(e.retry_after),
            )
        except BadRequest as e:
            logger.error(f"Telegram bad request sending message to {chat_id}: {str(e)}")
            return DeliveryResult(DeliveryResult.PERMANENT, error=str(e))
        except TelegramError as e:
            logger.error(f"Telegram error sending message to {chat_id}: {str(e)}")
            return DeliveryResult(DeliveryResult.TRANSIENT, error=str(e))
        except Exception as e:
            logger.error(f"Unexpected error sending message to {chat_id}: {str(e)}")
            return DeliveryResult(DeliveryResult.TRANSIENT, error=str(e))

    async def _send_rich_message(
        self,
        chat_id: int,
        text: str,
        entities: list[Any] | None,
        link_preview_options: Any,
    ) -> DeliveryResult:
        rich_html = build_rich_html(text, entities)
        if link_preview_options is not None:
            logger.info(
                "Rich message to %s cannot carry link preview options; ignoring them",
                chat_id,
            )
        try:
            result = await self.bot.do_api_request(
                "sendRichMessage",
                api_kwargs={"chat_id": chat_id, "rich_message": {"html": rich_html}},
            )
        except Forbidden as e:
            logger.warning(f"User {chat_id} has blocked the bot: {str(e)}")
            return DeliveryResult(DeliveryResult.BLOCKED, error=str(e))
        except RetryAfter as e:
            logger.warning(f"Flood control while sending rich message to {chat_id}: {str(e)}")
            return DeliveryResult(
                DeliveryResult.TRANSIENT,
                error=str(e),
                retry_after=_retry_after_seconds(e.retry_after),
            )
        except BadRequest as e:
            logger.error(f"Telegram bad request sending rich message to {chat_id}: {str(e)}")
            return DeliveryResult(DeliveryResult.PERMANENT, error=str(e))
        except TelegramError as e:
            logger.error(f"Telegram error sending rich message to {chat_id}: {str(e)}")
            return DeliveryResult(DeliveryResult.TRANSIENT, error=str(e))
        except Exception as e:
            logger.error(f"Unexpected error sending rich message to {chat_id}: {str(e)}")
            return DeliveryResult(DeliveryResult.TRANSIENT, error=str(e))
        message_id = _extract_message_id(result)
        logger.info(
            "Sent rich message to %s with %s code points and %s entities (msg_id: %s)",
            chat_id,
            len(text),
            len(entities) if entities else 0,
            message_id,
        )
        return DeliveryResult(DeliveryResult.SENT, message_id=message_id)

    def send_message_sync(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
        entities: list[Any] | None = None,
        link_preview_options: Any = None,
    ) -> DeliveryResult:
        loop = self._get_event_loop()

        try:
            result = loop.run_until_complete(
                self._send_message_async(
                    chat_id, text, parse_mode, entities, link_preview_options
                )
            )
            if not result.ok:
                logger.warning(f"Failed to send message to {chat_id}: {result.error}")
            return result
        except RuntimeError as e:
            logger.error(f"Runtime error in event loop: {str(e)}")
            self._loop = None
            loop = self._get_event_loop()

            try:
                result = loop.run_until_complete(
                    self._send_message_async(
                        chat_id, text, parse_mode, entities, link_preview_options
                    )
                )
                if not result.ok:
                    logger.error(f"Failed to send message after loop reset: {result.error}")
                return result
            except Exception as e:
                logger.error(f"Fatal error sending message to {chat_id}: {str(e)}")
                return DeliveryResult(DeliveryResult.TRANSIENT, error=str(e))
