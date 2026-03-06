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


def _reset_notification_hours(monkeypatch) -> None:
    monkeypatch.delenv("NOTIFICATION_START_HOUR", raising=False)
    monkeypatch.delenv("NOTIFICATION_END_HOUR", raising=False)
    importlib.reload(config)


def test_notification_hours_valid_override(monkeypatch):
    previous_start = os.environ.get("NOTIFICATION_START_HOUR")
    previous_end = os.environ.get("NOTIFICATION_END_HOUR")
    previous_skip = os.environ.get("PARTITA_SKIP_DOTENV")
    try:
        monkeypatch.setenv("NOTIFICATION_START_HOUR", "6")
        monkeypatch.setenv("NOTIFICATION_END_HOUR", "9")
        monkeypatch.setenv("PARTITA_SKIP_DOTENV", "true")
        importlib.reload(config)
        assert config.NOTIFICATION_START_HOUR == 6
        assert config.NOTIFICATION_END_HOUR == 9
    finally:
        if previous_start is None:
            monkeypatch.delenv("NOTIFICATION_START_HOUR", raising=False)
        else:
            monkeypatch.setenv("NOTIFICATION_START_HOUR", previous_start)
        if previous_end is None:
            monkeypatch.delenv("NOTIFICATION_END_HOUR", raising=False)
        else:
            monkeypatch.setenv("NOTIFICATION_END_HOUR", previous_end)
        if previous_skip is None:
            monkeypatch.delenv("PARTITA_SKIP_DOTENV", raising=False)
        else:
            monkeypatch.setenv("PARTITA_SKIP_DOTENV", previous_skip)
        importlib.reload(config)


def test_notification_hours_invalid_out_of_range(monkeypatch, caplog):
    previous_start = os.environ.get("NOTIFICATION_START_HOUR")
    previous_end = os.environ.get("NOTIFICATION_END_HOUR")
    previous_skip = os.environ.get("PARTITA_SKIP_DOTENV")
    try:
        monkeypatch.setenv("NOTIFICATION_START_HOUR", "25")
        monkeypatch.setenv("NOTIFICATION_END_HOUR", "9")
        monkeypatch.setenv("PARTITA_SKIP_DOTENV", "true")
        caplog.set_level(logging.WARNING)
        importlib.reload(config)
        assert config.NOTIFICATION_START_HOUR == config.DEFAULT_START_HOUR
        assert config.NOTIFICATION_END_HOUR == config.DEFAULT_END_HOUR
        assert "out of range" in caplog.text
    finally:
        if previous_start is None:
            monkeypatch.delenv("NOTIFICATION_START_HOUR", raising=False)
        else:
            monkeypatch.setenv("NOTIFICATION_START_HOUR", previous_start)
        if previous_end is None:
            monkeypatch.delenv("NOTIFICATION_END_HOUR", raising=False)
        else:
            monkeypatch.setenv("NOTIFICATION_END_HOUR", previous_end)
        if previous_skip is None:
            monkeypatch.delenv("PARTITA_SKIP_DOTENV", raising=False)
        else:
            monkeypatch.setenv("PARTITA_SKIP_DOTENV", previous_skip)
        importlib.reload(config)


def test_notification_hours_start_greater_than_or_equal_end(monkeypatch, caplog):
    previous_start = os.environ.get("NOTIFICATION_START_HOUR")
    previous_end = os.environ.get("NOTIFICATION_END_HOUR")
    previous_skip = os.environ.get("PARTITA_SKIP_DOTENV")
    try:
        monkeypatch.setenv("NOTIFICATION_START_HOUR", "12")
        monkeypatch.setenv("NOTIFICATION_END_HOUR", "10")
        monkeypatch.setenv("PARTITA_SKIP_DOTENV", "true")
        caplog.set_level(logging.WARNING)
        importlib.reload(config)
        assert config.NOTIFICATION_START_HOUR == config.DEFAULT_START_HOUR
        assert config.NOTIFICATION_END_HOUR == config.DEFAULT_END_HOUR
        assert ">=" in caplog.text or "Falling back" in caplog.text
    finally:
        if previous_start is None:
            monkeypatch.delenv("NOTIFICATION_START_HOUR", raising=False)
        else:
            monkeypatch.setenv("NOTIFICATION_START_HOUR", previous_start)
        if previous_end is None:
            monkeypatch.delenv("NOTIFICATION_END_HOUR", raising=False)
        else:
            monkeypatch.setenv("NOTIFICATION_END_HOUR", previous_end)
        if previous_skip is None:
            monkeypatch.delenv("PARTITA_SKIP_DOTENV", raising=False)
        else:
            monkeypatch.setenv("PARTITA_SKIP_DOTENV", previous_skip)
        importlib.reload(config)


def test_set_timezone_valid():
    original_tz = config.TIMEZONE
    original_tz_info = config.TIMEZONE_INFO
    try:
        config.set_timezone("America/New_York")
        assert config.TIMEZONE == "America/New_York"
        assert str(config.TIMEZONE_INFO) == "America/New_York"
    finally:
        config.TIMEZONE = original_tz
        config.TIMEZONE_INFO = original_tz_info


def test_set_timezone_invalid_fallback(caplog):
    original_tz = config.TIMEZONE
    original_tz_info = config.TIMEZONE_INFO
    try:
        caplog.set_level(logging.WARNING)
        config.set_timezone("Invalid/Timezone")
        assert config.TIMEZONE == "UTC"
        assert str(config.TIMEZONE_INFO) == "UTC"
        assert "Invalid timezone" in caplog.text
    finally:
        config.TIMEZONE = original_tz
        config.TIMEZONE_INFO = original_tz_info


def test_set_timezone_logs_info(caplog):
    original_tz = config.TIMEZONE
    original_tz_info = config.TIMEZONE_INFO
    try:
        caplog.set_level(logging.INFO)
        config.set_timezone("Asia/Tokyo")
        assert "Timezone set to Asia/Tokyo" in caplog.text
    finally:
        config.TIMEZONE = original_tz
        config.TIMEZONE_INFO = original_tz_info
