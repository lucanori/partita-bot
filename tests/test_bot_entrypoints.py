from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import partita_bot.bot as bot
import partita_bot.config as config


def test_create_conversation_handler_structure():
    handler = bot.create_conversation_handler()

    assert handler.entry_points
    assert handler.states[bot.WAITING_FOR_CITY][0].callback is bot.set_city
    fallback_callbacks = [fallback.callback for fallback in handler.fallbacks]
    assert fallback_callbacks[:2] == [bot.start, bot.show_keyboard]


class _StubApp:
    def __init__(self):
        self.handlers: list[object] = []
        self.error_handlers: list[object] = []
        self.polled = None

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler: object) -> None:
        self.error_handlers.append(handler)

    def run_polling(self, allowed_updates: list[str]) -> None:
        self.polled = allowed_updates


class _StubBot:
    def __init__(self) -> None:
        self.app = _StubApp()


def test_run_bot_with_provided_instance_records_handlers():
    stub_bot = _StubBot()
    bot.run_bot(stub_bot)

    assert stub_bot.app.polled == bot.Update.ALL_TYPES
    assert stub_bot.app.error_handlers == [bot.error_handler]
    handler_types = [type(handler).__name__ for handler in stub_bot.app.handlers]
    assert "CommandHandler" in handler_types[0]
    assert "CommandHandler" in handler_types[1]
    assert "ConversationHandler" in handler_types[2]


def test_run_bot_without_instance_initializes_bot(monkeypatch):
    stub_bot = _StubBot()
    calls: list[str] = []

    def fake_get_bot(token: str) -> _StubBot:
        calls.append(token)
        return stub_bot

    monkeypatch.setattr(bot, "get_bot", fake_get_bot)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "abc")

    bot.run_bot()

    assert calls == ["abc"]
    assert stub_bot.app.polled == bot.Update.ALL_TYPES


def test_start_admin_interface_debug(monkeypatch):
    monkeypatch.setattr(config, "DEBUG", True)

    class _DummyThread:
        def __init__(self, target, *args, **kwargs):
            self.target = target
            self.daemon = False
            self.started = False
            created.append(self)

        def start(self) -> None:
            self.started = True

    created: list[_DummyThread] = []

    monkeypatch.setattr(bot.threading, "Thread", _DummyThread)

    bot.start_admin_interface()

    thread = created[0]
    assert thread.daemon is True
    assert thread.started is True
    assert thread.target is bot.run_admin_interface


def test_start_admin_interface_production(monkeypatch):
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setattr(config, "ADMIN_PORT", 8888)
    commands: list[str] = []

    def fake_system(command: str) -> int:  # pragma: no cover - simple stub
        commands.append(command)
        return 0

    monkeypatch.setattr(bot.os, "system", fake_system)

    class _Thread:
        def __init__(self, target, *args, **kwargs):
            self.target = target
            self.daemon = False
            self.started = False
            created.append(self)

        def start(self) -> None:
            self.started = True
            self.target()

    created: list[_Thread] = []

    monkeypatch.setattr(bot.threading, "Thread", _Thread)

    bot.start_admin_interface()

    assert commands
    assert "--bind 0.0.0.0:8888" in commands[0]
    assert "wsgi:application" in commands[0]
    thread = created[0]
    assert thread.daemon is True
    assert thread.started is True


def test_main_starts_services_when_not_imported(monkeypatch):
    calls: list[str] = []

    def fake_get_bot(token: str) -> None:
        calls.append(f"get_bot:{token}")

    def fake_start_admin() -> None:
        calls.append("start_admin")

    def fake_run_bot(instance: object | None = None) -> None:
        calls.append("run_bot")

    monkeypatch.setattr(bot, "get_bot", fake_get_bot)
    monkeypatch.setattr(bot, "start_admin_interface", fake_start_admin)
    monkeypatch.setattr(bot, "run_bot", fake_run_bot)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token")

    monkeypatch.setattr(sys, "argv", ["run_bot.py"])
    monkeypatch.delitem(sys.modules, "gunicorn", raising=False)

    bot.main()

    assert "get_bot:token" in calls
    assert "start_admin" in calls
    assert "run_bot" in calls


def test_main_skips_services_under_wsgi(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token")
    calls: list[str] = []

    def fake_get_bot(token: str) -> None:
        calls.append(f"get_bot:{token}")

    monkeypatch.setattr(bot, "get_bot", fake_get_bot)
    monkeypatch.setattr(bot, "start_admin_interface", lambda: pytest.fail("should not start admin"))
    monkeypatch.setattr(bot, "run_bot", lambda *args, **kwargs: pytest.fail("should not poll"))

    monkeypatch.setitem(sys.modules, "gunicorn", SimpleNamespace())
    monkeypatch.setattr(sys, "argv", ["gunicorn", "wsgi:app"])

    bot.main()

    assert "get_bot:token" in calls
