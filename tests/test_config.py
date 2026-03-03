import importlib
import logging
import os

import partita_bot.config as config


def _reset_config(
    monkeypatch,
    previous_value: str | None,
    previous_skip: str | None = None,
) -> None:
    if previous_value is None:
        monkeypatch.delenv("EXA_API_KEY", raising=False)
    else:
        monkeypatch.setenv("EXA_API_KEY", previous_value)
    if previous_skip is None:
        monkeypatch.delenv("PARTITA_SKIP_DOTENV", raising=False)
    else:
        monkeypatch.setenv("PARTITA_SKIP_DOTENV", previous_skip)
    importlib.reload(config)


def test_exa_api_key_reload(monkeypatch):
    previous = os.environ.get("EXA_API_KEY")
    try:
        monkeypatch.setenv("EXA_API_KEY", "test-exa-key")
        importlib.reload(config)
        assert config.EXA_API_KEY == "test-exa-key"
    finally:
        _reset_config(monkeypatch, previous)


def test_missing_exa_api_key_logs(monkeypatch, caplog):
    previous = os.environ.get("EXA_API_KEY")
    previous_skip = os.environ.get("PARTITA_SKIP_DOTENV")
    try:
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.setenv("PARTITA_SKIP_DOTENV", "true")
        caplog.set_level(logging.ERROR)
        importlib.reload(config)
        assert "EXA_API_KEY" in caplog.text
    finally:
        _reset_config(monkeypatch, previous, previous_skip)


def test_bot_language_default_is_english(monkeypatch):
    previous = os.environ.get("BOT_LANGUAGE")
    previous_skip = os.environ.get("PARTITA_SKIP_DOTENV")
    try:
        monkeypatch.delenv("BOT_LANGUAGE", raising=False)
        monkeypatch.setenv("PARTITA_SKIP_DOTENV", "true")
        importlib.reload(config)
        assert config.BOT_LANGUAGE == "English"
    finally:
        if previous is None:
            monkeypatch.delenv("BOT_LANGUAGE", raising=False)
        else:
            monkeypatch.setenv("BOT_LANGUAGE", previous)
        if previous_skip is None:
            monkeypatch.delenv("PARTITA_SKIP_DOTENV", raising=False)
        else:
            monkeypatch.setenv("PARTITA_SKIP_DOTENV", previous_skip)
        importlib.reload(config)


def test_bot_language_reload(monkeypatch):
    previous = os.environ.get("BOT_LANGUAGE")
    previous_skip = os.environ.get("PARTITA_SKIP_DOTENV")
    try:
        monkeypatch.setenv("BOT_LANGUAGE", "Italian")
        monkeypatch.setenv("PARTITA_SKIP_DOTENV", "true")
        importlib.reload(config)
        assert config.BOT_LANGUAGE == "Italian"
    finally:
        if previous is None:
            monkeypatch.delenv("BOT_LANGUAGE", raising=False)
        else:
            monkeypatch.setenv("BOT_LANGUAGE", previous)
        if previous_skip is None:
            monkeypatch.delenv("PARTITA_SKIP_DOTENV", raising=False)
        else:
            monkeypatch.setenv("PARTITA_SKIP_DOTENV", previous_skip)
        importlib.reload(config)
