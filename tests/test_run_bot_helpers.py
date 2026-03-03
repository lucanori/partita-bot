import asyncio
from types import SimpleNamespace
from typing import cast

import run_bot
from partita_bot.storage import Database


class DummyResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_check_telegram_token_conflict(monkeypatch):
    monkeypatch.setattr(run_bot.requests, "get", lambda url, timeout: DummyResponse(409))
    assert run_bot.check_telegram_token_in_use("token")


def test_check_telegram_token_available(monkeypatch):
    monkeypatch.setattr(run_bot.requests, "get", lambda url, timeout: DummyResponse(200))
    assert not run_bot.check_telegram_token_in_use("token")


def test_check_telegram_token_handles_errors(monkeypatch):
    def raise_exc(url, timeout):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_bot.requests, "get", raise_exc)
    assert not run_bot.check_telegram_token_in_use("token")


class StubBot:
    sent: list[tuple[int, str]]

    def __init__(self, succeed: bool = True):
        self.sent = []
        self.succeed = succeed

    def send_message_sync(self, chat_id: int, text: str) -> bool:
        self.sent.append((chat_id, text))
        return self.succeed


class StubDB:
    def __init__(self):
        self.marked: list[int] = []

    def mark_message_sent(self, message_id: int) -> None:
        self.marked.append(message_id)


class AdminDB:
    def __init__(self):
        self.marked: list[int] = []
        self.seen: list[tuple[str, int]] = []

    async def remove_blocked_users(self, bot):
        self.seen.append((getattr(bot, "name", "bot"), 0))
        return {"removed_users": 1, "total_users": 2, "errors": []}

    def mark_message_sent(self, message_id: int) -> None:
        self.marked.append(message_id)


class FailingAdminDB(AdminDB):
    async def remove_blocked_users(self, bot):
        raise RuntimeError("boom")


def test_process_admin_operation_marks_message():
    db = AdminDB()
    fake_bot = SimpleNamespace(name="cleanup")
    asyncio.run(run_bot.process_admin_operation(fake_bot, "CLEANUP_USERS", 42, cast(Database, db)))
    assert db.marked == [42]


def test_process_admin_operation_handles_failure_and_still_marks():
    db = FailingAdminDB()
    asyncio.run(
        run_bot.process_admin_operation(
            SimpleNamespace(name="cleanup"), "CLEANUP_USERS", 99, cast(Database, db)
        )
    )
    assert db.marked == [99]


def test_process_queued_message_regular_success():
    bot = StubBot(succeed=True)
    db = StubDB()
    message = SimpleNamespace(telegram_id=5, message="hola", id=1)
    run_bot.process_queued_message(bot, cast(Database, db), message)
    assert db.marked == [1]
    assert bot.sent == [(5, "hola")]


def test_process_queued_message_regular_failure():
    bot = StubBot(succeed=False)
    db = StubDB()
    message = SimpleNamespace(telegram_id=7, message="ciao", id=2)
    run_bot.process_queued_message(bot, cast(Database, db), message)
    assert db.marked == []
    assert bot.sent == [(7, "ciao")]


def test_process_queued_message_admin_operation(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_admin(bot_instance, operation: str, message_id: int, db):
        calls.append((operation, message_id))

    monkeypatch.setattr(run_bot, "process_admin_operation", fake_admin)
    message = SimpleNamespace(
        telegram_id=0,
        message="ADMIN_OPERATION:CLEANUP_USERS",
        id=33,
    )
    db = AdminDB()
    bot = SimpleNamespace(name="admin")
    run_bot.process_queued_message(bot, cast(Database, db), message)
    assert calls == [("CLEANUP_USERS", 33)]
