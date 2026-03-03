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

    def __init__(self, succeed: bool = True, message_id: int | None = 123):
        self.sent = []
        self.succeed = succeed
        self.error_message = "Failed to send"
        self.message_id = message_id

    def send_message_sync(self, chat_id: int, text: str) -> tuple[bool, str | None, int | None]:
        self.sent.append((chat_id, text))
        if self.succeed:
            return True, None, self.message_id
        return False, self.error_message, None


class StubDB:
    def __init__(self):
        self.marked: list[tuple[int, int | None]] = []
        self.blocked: list[int] = []

    def mark_message_sent(self, message_id: int, sent_message_id: int | None = None) -> None:
        self.marked.append((message_id, sent_message_id))

    def mark_user_blocked(self, telegram_id: int) -> None:
        self.blocked.append(telegram_id)


class AdminDB:
    def __init__(self):
        self.marked: list[int] = []
        self.seen: list[tuple[str, int]] = []

    async def recheck_blocked_users(self, bot):
        self.seen.append((getattr(bot, "name", "bot"), 0))
        return {"checked": 2, "unblocked": 1, "still_blocked": 1, "errors": []}

    def mark_message_sent(self, message_id: int, sent_message_id: int | None = None) -> None:
        self.marked.append(message_id)


class FailingAdminDB(AdminDB):
    async def recheck_blocked_users(self, bot):
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
    assert db.marked == [(1, 123)]
    assert bot.sent == [(5, "hola")]


def test_process_queued_message_regular_failure():
    bot = StubBot(succeed=False)
    db = StubDB()
    message = SimpleNamespace(telegram_id=7, message="ciao", id=2)
    run_bot.process_queued_message(bot, cast(Database, db), message)
    assert db.marked == []
    assert bot.sent == [(7, "ciao")]


def test_process_queued_message_blocked_user():
    bot = StubBot(succeed=False)
    bot.error_message = "Forbidden: bot was blocked"
    db = StubDB()
    message = SimpleNamespace(telegram_id=9, message="ciao", id=3)
    run_bot.process_queued_message(bot, cast(Database, db), message)
    assert db.marked == [(3, None)]
    assert db.blocked == [9]
    assert bot.sent == [(9, "ciao")]


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


def test_process_queued_message_applies_rate_limit_sleep():
    bot = StubBot(succeed=True)
    db = StubDB()
    message = SimpleNamespace(telegram_id=5, message="hola", id=1)

    sleep_calls: list[float] = []

    def stub_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    run_bot.process_queued_message(bot, cast(Database, db), message, sleep_fn=stub_sleep)
    assert db.marked == [(1, 123)]
    assert bot.sent == [(5, "hola")]
    assert sleep_calls == [1.0]


def test_process_queued_message_no_sleep_when_none_provided():
    bot = StubBot(succeed=True)
    db = StubDB()
    message = SimpleNamespace(telegram_id=5, message="hola", id=1)
    run_bot.process_queued_message(bot, cast(Database, db), message, sleep_fn=None)
    assert db.marked == [(1, 123)]
    assert bot.sent == [(5, "hola")]


def test_process_queued_message_admin_operation_no_rate_limit():
    calls: list[tuple[str, int]] = []

    async def fake_admin(bot_instance, operation: str, message_id: int, db):
        calls.append((operation, message_id))

    import run_bot

    run_bot.process_admin_operation = fake_admin  # type: ignore

    message = SimpleNamespace(
        telegram_id=0,
        message="ADMIN_OPERATION:CLEANUP_USERS",
        id=33,
    )
    db = AdminDB()
    bot = SimpleNamespace(name="admin")

    sleep_calls: list[float] = []

    def stub_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    run_bot.process_queued_message(bot, cast(Database, db), message, sleep_fn=stub_sleep)
    assert calls == [("CLEANUP_USERS", 33)]
    assert sleep_calls == []


class StubDBWithMessageId:
    def __init__(self):
        self.marked: list[tuple[int, int | None]] = []
        self.blocked: list[int] = []

    def mark_message_sent(self, message_id: int, sent_message_id: int | None = None) -> None:
        self.marked.append((message_id, sent_message_id))

    def mark_user_blocked(self, telegram_id: int) -> None:
        self.blocked.append(telegram_id)


def test_process_queued_message_captures_message_id():
    bot = StubBot(succeed=True, message_id=456)
    db = StubDBWithMessageId()
    message = SimpleNamespace(telegram_id=5, message="hola", id=1)
    run_bot.process_queued_message(bot, cast(Database, db), message)
    assert db.marked == [(1, 456)]
    assert bot.sent == [(5, "hola")]


def test_process_queued_message_no_message_id_on_failure():
    bot = StubBot(succeed=False, message_id=None)
    db = StubDBWithMessageId()
    message = SimpleNamespace(telegram_id=7, message="ciao", id=2)
    run_bot.process_queued_message(bot, cast(Database, db), message)
    assert db.marked == []
    assert bot.sent == [(7, "ciao")]


class DeleteSentDB:
    def __init__(self):
        self.marked: list[int] = []
        self.deleted_calls: list[tuple[int, int]] = []

    async def delete_sent_messages_for_user_within_hours(
        self, bot, telegram_id: int, hours: int = 1
    ):
        self.deleted_calls.append((telegram_id, hours))
        return {"success_count": 2, "error_count": 1, "total_attempted": 3, "errors": []}

    def mark_message_sent(self, message_id: int, sent_message_id: int | None = None) -> None:
        self.marked.append(message_id)
