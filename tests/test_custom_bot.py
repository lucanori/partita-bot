from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from telegram import MessageEntity
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError, TimedOut

import partita_bot.custom_bot as custom_bot
from partita_bot.custom_bot import DeliveryResult


class DummyTelegramBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.send_kwargs: list[dict] = []
        self.fail_message: bool = False
        self.message_error: Exception | None = None
        self.message_id_counter: int = 100
        self.rich_calls: list[dict] = []
        self.rich_response: object = {"message_id": 777}
        self.rich_error: Exception | None = None

    async def send_message(self, chat_id: int, text: str, **kwargs):
        if self.message_error is not None:
            raise self.message_error
        if self.fail_message:
            raise TelegramError("boom")
        self.sent.append((chat_id, text))
        self.send_kwargs.append(kwargs)
        self.message_id_counter += 1
        return type("Message", (), {"message_id": self.message_id_counter})()

    async def do_api_request(self, endpoint, api_kwargs=None, **kwargs):
        if self.rich_error is not None:
            raise self.rich_error
        self.rich_calls.append({"endpoint": endpoint, "api_kwargs": api_kwargs})
        return self.rich_response


class DummyApplication:
    last_builder: DummyApplication.Builder | None = None

    class Builder:
        def __init__(self, app: "DummyApplication"):
            self.app = app
            self.tokens: list[str] = []
            DummyApplication.last_builder = self

        def token(self, token: str) -> "DummyApplication.Builder":
            self.tokens.append(token)
            return self

        def build(self) -> "DummyApplication":
            return self.app

    def __init__(self):
        self.bot = DummyTelegramBot()

    @classmethod
    def builder(cls) -> "DummyApplication.Builder":
        return DummyApplication.Builder(DummyApplication())


def test_bot_requires_token():
    with pytest.raises(ValueError):
        custom_bot.Bot("")


def _make_bot(monkeypatch) -> custom_bot.Bot:
    monkeypatch.setattr(custom_bot, "Application", DummyApplication)
    return custom_bot.Bot("token")


def test_send_message_sync_success(monkeypatch):
    monkeypatch.setattr(custom_bot, "Application", DummyApplication)
    bot = custom_bot.Bot("token")
    success, error, message_id = bot.send_message_sync(chat_id=123, text="hey")
    assert success
    assert error is None
    assert message_id is not None
    assert message_id > 0
    assert bot.bot.sent == [(123, "hey")]
    builder = DummyApplication.last_builder
    assert builder is not None
    assert builder.tokens == ["token"]


def test_send_message_sync_handles_telegram_error(monkeypatch):
    monkeypatch.setattr(custom_bot, "Application", DummyApplication)
    bot = custom_bot.Bot("token")
    bot.bot.fail_message = True
    success, error, message_id = bot.send_message_sync(chat_id=99, text="fail")
    assert not success
    assert isinstance(error, str) and "boom" in error
    assert message_id is None


def test_send_message_sync_recovers_after_runtime_error(monkeypatch):
    monkeypatch.setattr(custom_bot, "Application", DummyApplication)
    bot = custom_bot.Bot("token")

    class LoopStub:
        def __init__(self, should_raise: bool):
            self.should_raise = should_raise

        def run_until_complete(self, coro):
            if self.should_raise:
                coro.close()
                raise RuntimeError("loop failure")
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

    generator = iter([LoopStub(True), LoopStub(False)])

    def fake_get_event_loop(self):
        return next(generator)

    monkeypatch.setattr(custom_bot.Bot, "_get_event_loop", fake_get_event_loop)
    success, error, message_id = bot.send_message_sync(chat_id=101, text="retry")
    assert success
    assert error is None
    assert message_id is not None
    assert bot.bot.sent[-1] == (101, "retry")


class TestDeliveryResultContract:
    def test_terminal_statuses(self):
        assert DeliveryResult(DeliveryResult.SENT).is_terminal
        assert DeliveryResult(DeliveryResult.BLOCKED).is_terminal
        assert DeliveryResult(DeliveryResult.PERMANENT).is_terminal
        assert not DeliveryResult(DeliveryResult.TRANSIENT).is_terminal

    def test_only_transient_is_retryable(self):
        assert DeliveryResult(DeliveryResult.TRANSIENT).is_retryable
        assert not DeliveryResult(DeliveryResult.SENT).is_retryable
        assert not DeliveryResult(DeliveryResult.PERMANENT).is_retryable
        assert not DeliveryResult(DeliveryResult.BLOCKED).is_retryable

    def test_blocked_flag(self):
        assert DeliveryResult(DeliveryResult.BLOCKED).blocked
        assert not DeliveryResult(DeliveryResult.SENT).blocked

    def test_iter_yields_legacy_tuple_shape(self):
        success, error, message_id = DeliveryResult(
            DeliveryResult.SENT, error=None, message_id=7
        )
        assert success is True
        assert error is None
        assert message_id == 7

    def test_failure_iter_yields_false(self):
        success, error, message_id = DeliveryResult(
            DeliveryResult.TRANSIENT, error="timed out"
        )
        assert success is False
        assert error == "timed out"
        assert message_id is None


class TestRetryAfterNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (9, 9.0),
            (0, 0.0),
            (2.5, 2.5),
            (timedelta(seconds=2), 2.0),
            (None, None),
            ("soon", None),
            (-3, None),
            (float("inf"), None),
            (float("nan"), None),
            (object(), None),
        ],
    )
    def test_retry_after_seconds_normalizes_raw_values(self, raw, expected):
        assert custom_bot._retry_after_seconds(raw) == expected

    def test_negative_retry_after_error_is_transient_without_delay(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.message_error = RetryAfter(-3)
        result = bot.send_message_sync(chat_id=1, text="short")
        assert result.is_retryable
        assert result.retry_after is None


class TestShortMessagePath:
    def test_4096_code_points_use_send_message(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        text = "a" * custom_bot.MAX_SEND_TEXT_LENGTH
        result = bot.send_message_sync(chat_id=1, text=text)
        assert result.ok
        assert result.message_id is not None
        assert bot.bot.sent == [(1, text)]
        assert bot.bot.send_kwargs == [{}]
        assert bot.bot.rich_calls == []

    def test_entities_and_link_preview_options_unchanged_on_short_path(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        entities = [MessageEntity("bold", 0, 4)]
        bot.send_message_sync(
            chat_id=1,
            text="bold text",
            entities=entities,
            link_preview_options={"is_disabled": True},
        )
        assert bot.bot.send_kwargs == [
            {"entities": entities, "link_preview_options": {"is_disabled": True}}
        ]

    def test_bad_request_is_permanent(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.message_error = BadRequest("can not parse entities")
        result = bot.send_message_sync(chat_id=1, text="short")
        assert result.status == custom_bot.STATUS_PERMANENT
        assert result.is_terminal
        assert result.message_id is None

    def test_retry_after_is_transient_with_delay(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.message_error = RetryAfter(9)
        result = bot.send_message_sync(chat_id=1, text="short")
        assert result.is_retryable
        assert result.retry_after == 9.0

    def test_unexpected_error_is_transient(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.message_error = RuntimeError("disk on fire")
        result = bot.send_message_sync(chat_id=1, text="short")
        assert result.is_retryable
        assert result.error is not None


class TestLongRichMessagePath:
    def test_4097_code_points_send_exact_sendrichmessage_payload(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        text = "a" * (custom_bot.MAX_SEND_TEXT_LENGTH + 1)
        result = bot.send_message_sync(chat_id=42, text=text)
        assert result.ok
        assert result.message_id == 777
        assert bot.bot.sent == []
        assert bot.bot.rich_calls == [
            {
                "endpoint": "sendRichMessage",
                "api_kwargs": {"chat_id": 42, "rich_message": {"html": text}},
            }
        ]

    def test_7123_code_points_send_one_message_without_splitting(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        text = "x" * 7123
        result = bot.send_message_sync(chat_id=43, text=text)
        assert result.ok
        assert len(bot.bot.rich_calls) == 1
        assert bot.bot.rich_calls[0]["api_kwargs"]["rich_message"]["html"] == text

    def test_32768_code_points_are_sent_rich(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        text = "y" * custom_bot.MAX_RICH_TEXT_LENGTH
        result = bot.send_message_sync(chat_id=44, text=text)
        assert result.ok
        assert len(bot.bot.rich_calls) == 1

    def test_32769_code_points_fail_permanently_without_any_api_call(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        text = "z" * (custom_bot.MAX_RICH_TEXT_LENGTH + 1)
        result = bot.send_message_sync(chat_id=45, text=text)
        assert result.status == custom_bot.STATUS_PERMANENT
        assert result.is_terminal
        assert result.message_id is None
        assert bot.bot.rich_calls == []
        assert bot.bot.sent == []

    def test_rich_payload_carries_no_parse_mode_entities_or_link_preview(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        text = "b" * 5000
        bot.send_message_sync(
            chat_id=3,
            text=text,
            parse_mode="HTML",
            link_preview_options={"is_disabled": True},
        )
        assert len(bot.bot.rich_calls) == 1
        api_kwargs = bot.bot.rich_calls[0]["api_kwargs"]
        assert set(api_kwargs.keys()) == {"chat_id", "rich_message"}
        assert set(api_kwargs["rich_message"].keys()) == {"html"}
        assert api_kwargs["rich_message"]["html"] == text

    def test_bold_entity_becomes_html_tags_in_payload(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        prefix = "p" * 4090
        text = prefix + "bold tail"
        entities = [MessageEntity("bold", 4090, 9)]
        result = bot.send_message_sync(chat_id=4, text=text, entities=entities)
        assert result.ok
        payload = bot.bot.rich_calls[0]["api_kwargs"]["rich_message"]["html"]
        assert payload == f"{prefix}<b>bold tail</b>"

    def test_utf16_entity_offsets_survive_emoji_prefix(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        text = "\U0001f3af " + "z" * 4100
        entities = [MessageEntity("bold", 3, 4100)]
        result = bot.send_message_sync(chat_id=5, text=text, entities=entities)
        assert result.ok
        payload = bot.bot.rich_calls[0]["api_kwargs"]["rich_message"]["html"]
        assert payload == "\U0001f3af <b>" + "z" * 4100 + "</b>"

    def test_malformed_entities_fall_back_to_escaped_plain_rich_html(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        text = "body & <tail> " + "q" * 5000
        entities = [MessageEntity("bold", 0, custom_bot.MAX_RICH_TEXT_LENGTH + 5)]
        result = bot.send_message_sync(chat_id=6, text=text, entities=entities)
        assert result.ok
        payload = bot.bot.rich_calls[0]["api_kwargs"]["rich_message"]["html"]
        assert payload == f"body &amp; &lt;tail&gt; {'q' * 5000}"

    def test_message_id_extracted_from_dict_response(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.rich_response = {"message_id": 555, "ok": True}
        result = bot.send_message_sync(chat_id=1, text="x" * 5000)
        assert result.ok
        assert result.message_id == 555

    def test_message_id_extracted_from_object_response(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.rich_response = SimpleNamespace(message_id=556)
        result = bot.send_message_sync(chat_id=1, text="x" * 5000)
        assert result.ok
        assert result.message_id == 556

    def test_missing_message_id_still_sends_successfully(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.rich_response = {"ok": True}
        result = bot.send_message_sync(chat_id=1, text="x" * 5000)
        assert result.ok
        assert result.message_id is None

    def test_forbidden_is_blocked(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.rich_error = Forbidden("Forbidden: bot was blocked by the user")
        result = bot.send_message_sync(chat_id=7, text="x" * 5000)
        assert result.status == custom_bot.STATUS_BLOCKED
        assert result.is_terminal

    def test_retry_after_is_transient_with_delay(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.rich_error = RetryAfter(21)
        result = bot.send_message_sync(chat_id=7, text="x" * 5000)
        assert result.is_retryable
        assert result.retry_after == 21.0

    def test_bad_request_is_permanent(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.rich_error = BadRequest("Bad Request: rich message is invalid")
        result = bot.send_message_sync(chat_id=7, text="x" * 5000)
        assert result.status == custom_bot.STATUS_PERMANENT
        assert result.is_terminal

    def test_timed_out_is_transient(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.rich_error = TimedOut()
        result = bot.send_message_sync(chat_id=7, text="x" * 5000)
        assert result.is_retryable

    def test_unexpected_error_is_transient(self, monkeypatch):
        bot = _make_bot(monkeypatch)
        bot.bot.rich_error = RuntimeError("connection reset")
        result = bot.send_message_sync(chat_id=7, text="x" * 5000)
        assert result.is_retryable
        assert result.error is not None
