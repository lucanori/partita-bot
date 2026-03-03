import importlib
import sys

import partita_bot.bot_manager as bot_manager
import partita_bot.custom_bot as custom_bot


class DummyApplication:
    class Builder:
        def __init__(self):
            self.app = DummyApplication()

        def token(self, token):
            return self

        def build(self):
            return self.app

    @classmethod
    def builder(cls):
        return DummyApplication.Builder()


def _reload_wsgi():
    if "wsgi" in sys.modules:
        del sys.modules["wsgi"]
    return importlib.import_module("wsgi")


def test_wsgi_initializes_bot(monkeypatch):
    monkeypatch.setattr(custom_bot, "Application", DummyApplication)
    monkeypatch.setattr(bot_manager, "is_bot_initialized", lambda: False)
    calls: list[str] = []

    def fake_get_bot(token):
        calls.append(token)

    monkeypatch.setattr(bot_manager, "get_bot", fake_get_bot)
    _reload_wsgi()
    assert calls


def test_wsgi_skips_initialization(monkeypatch):
    monkeypatch.setattr(custom_bot, "Application", DummyApplication)
    monkeypatch.setattr(bot_manager, "is_bot_initialized", lambda: True)
    calls: list[str] = []

    def fake_get_bot(token):
        calls.append(token)

    monkeypatch.setattr(bot_manager, "get_bot", fake_get_bot)
    _reload_wsgi()
    assert not calls
