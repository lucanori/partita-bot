from __future__ import annotations

import asyncio

import pytest
from telegram.error import TelegramError

import partita_bot.custom_bot as custom_bot


class DummyTelegramBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.fail_message: bool = False
        self.message_id_counter: int = 100

    async def send_message(self, chat_id: int, text: str):
        if self.fail_message:
            raise TelegramError("boom")
        self.sent.append((chat_id, text))
        self.message_id_counter += 1
        return type("Message", (), {"message_id": self.message_id_counter})()


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
