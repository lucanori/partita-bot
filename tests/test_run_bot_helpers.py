from types import SimpleNamespace
from typing import cast

import pytest

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
        self.admin_marked: list[int] = []
        self.seen: list[tuple[str, int]] = []

    async def recheck_blocked_users(self, bot):
        self.seen.append((getattr(bot, "name", "bot"), 0))
        return {"checked": 2, "unblocked": 1, "still_blocked": 1, "errors": []}

    def mark_message_sent(self, message_id: int, sent_message_id: int | None = None) -> None:
        self.marked.append(message_id)

    def mark_admin_operation_processed(self, operation_id: int) -> bool:
        self.admin_marked.append(operation_id)
        return True


class FailingAdminDB(AdminDB):
    async def recheck_blocked_users(self, bot):
        raise RuntimeError("boom")


@pytest.mark.anyio
async def test_process_admin_operation_marks_message():
    db = AdminDB()
    fake_bot = SimpleNamespace(name="cleanup")
    await run_bot.process_admin_operation(
        fake_bot, "CLEANUP_USERS", 42, cast(Database, db), is_legacy=True
    )
    assert db.marked == [42]


@pytest.mark.anyio
async def test_process_admin_operation_marks_admin_queue():
    db = AdminDB()
    fake_bot = SimpleNamespace(name="cleanup")
    await run_bot.process_admin_operation(
        fake_bot, "RECHECK_BLOCKED_USERS", 42, cast(Database, db), is_legacy=False
    )
    assert db.admin_marked == [42]


@pytest.mark.anyio
async def test_process_admin_operation_handles_failure_and_still_marks():
    db = FailingAdminDB()
    await run_bot.process_admin_operation(
        SimpleNamespace(name="cleanup"), "CLEANUP_USERS", 99, cast(Database, db), is_legacy=True
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
    calls: list[tuple[str, int, bool]] = []

    async def fake_admin(
        bot_instance, operation: str, message_id: int, db, params=None, is_legacy=False
    ):
        calls.append((operation, message_id, is_legacy))

    monkeypatch.setattr(run_bot, "process_admin_operation", fake_admin)
    message = SimpleNamespace(
        telegram_id=0,
        message="ADMIN_OPERATION:CLEANUP_USERS",
        id=33,
    )
    db = AdminDB()
    bot = SimpleNamespace(name="admin")
    run_bot.process_queued_message(bot, cast(Database, db), message)
    assert calls == [("CLEANUP_USERS", 33, True)]


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
    calls: list[tuple[str, int, bool]] = []

    async def fake_admin(
        bot_instance, operation: str, message_id: int, db, params=None, is_legacy=False
    ):
        calls.append((operation, message_id, is_legacy))

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
    assert calls == [("CLEANUP_USERS", 33, True)]
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


@pytest.mark.anyio
async def test_process_admin_operation_delete_sent():
    db = DeleteSentDB()
    fake_bot = SimpleNamespace(name="delete")
    await run_bot.process_admin_operation(
        fake_bot,
        "DELETE_SENT_LAST_HOURS",
        42,
        cast(Database, db),
        params=["123", "1"],
        is_legacy=True,
    )
    assert db.marked == [42]
    assert db.deleted_calls == [(123, 1)]


class NotifyAllDB:
    def __init__(self):
        self.marked: list[int] = []
        self.users: list = []

    def get_all_users(self):
        return self.users

    def mark_message_sent(self, message_id: int, sent_message_id: int | None = None) -> None:
        self.marked.append(message_id)


@pytest.mark.anyio
async def test_process_admin_operation_notify_all(monkeypatch):
    db = NotifyAllDB()
    db.users = [SimpleNamespace(telegram_id=1, city="roma")]
    fake_bot = SimpleNamespace(name="notify")

    monkeypatch.setattr(
        run_bot, "process_notifications", lambda **kwargs: {"notifications_sent": 1}
    )

    await run_bot.process_admin_operation(
        fake_bot, "NOTIFY_ALL_USERS", 42, cast(Database, db), is_legacy=True
    )
    assert db.marked == [42]


class NotifySingleDB:
    def __init__(self):
        self.marked: list[int] = []
        self.user: object | None = None
        self.cities: list[str] = []
        self.queued: list[tuple[int, str]] = []
        self.manual_updated: list[int] = []

    def get_user(self, user_id: int):
        return self.user

    def can_send_manual_notification(self, user_id: int, cooldown_minutes: int = 5) -> bool:
        return True

    def get_user_cities(self, user_id: int) -> list[str]:
        return self.cities

    def queue_message(self, telegram_id: int, message: str) -> bool:
        self.queued.append((telegram_id, message))
        return True

    def update_last_notification(self, telegram_id: int, is_manual: bool = False):
        self.manual_updated.append(telegram_id)

    def mark_message_sent(self, message_id: int, sent_message_id: int | None = None) -> None:
        self.marked.append(message_id)


@pytest.mark.anyio
async def test_process_admin_operation_notify_single_success(monkeypatch):
    db = NotifySingleDB()
    db.user = SimpleNamespace(telegram_id=123)
    db.cities = ["roma"]
    fake_bot = SimpleNamespace(name="notify")

    class FakeFetcher:
        def fetch_event_message(self, city: str, date):
            return f"Events for {city}"

    monkeypatch.setattr(run_bot, "EventFetcher", lambda db: FakeFetcher())

    await run_bot.process_admin_operation(
        fake_bot, "NOTIFY_SINGLE_USER", 42, cast(Database, db), params=["123"], is_legacy=True
    )
    assert db.marked == [42]
    assert db.queued == [(123, "Events for roma")]
    assert db.manual_updated == [123]


@pytest.mark.anyio
async def test_process_admin_operation_unknown_op():
    db = AdminDB()
    fake_bot = SimpleNamespace(name="unknown")
    await run_bot.process_admin_operation(
        fake_bot, "UNKNOWN_OP", 42, cast(Database, db), is_legacy=True
    )
    assert db.marked == [42]
