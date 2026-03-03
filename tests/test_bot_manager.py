import importlib
import os
import threading

import partita_bot.bot_manager as bot_manager
import partita_bot.custom_bot as custom_bot


class StubBot:
    def __init__(self, token: str):
        self.token = token


def _reset_bot_manager():
    importlib.reload(bot_manager)


def test_get_bot_singleton(monkeypatch):
    monkeypatch.setattr(custom_bot, "Bot", StubBot)
    _reset_bot_manager()
    monkeypatch.setattr(bot_manager, "_bot_instance", None)
    monkeypatch.setattr(bot_manager, "_initialized", False)
    result = bot_manager.get_bot("abc")
    assert isinstance(result, StubBot)
    second = bot_manager.get_bot("abc")
    assert second is result
    info = bot_manager.get_owner_info()
    assert info["initialized"]
    assert info["process_id"] == os.getpid()
    assert info["thread_id"] == threading.get_ident()
