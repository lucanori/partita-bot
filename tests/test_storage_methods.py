import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from partita_bot.storage import Database


@pytest.fixture
def db():
    database = Database(database_url="sqlite:///:memory:")
    try:
        yield database
    finally:
        database.close()


def test_blocklist_enforces_rules(db):
    db.add_user(1, "alice", "Milano")
    assert db.check_access(1)
    db.add_to_list("blocklist", 1)
    assert not db.check_access(1)
    db.remove_from_list("blocklist", 1)
    assert db.check_access(1)


def test_whitelist_mode_requirements(db):
    db.add_to_list("whitelist", 2)
    db.set_access_mode("whitelist")
    assert db.check_access(2)
    assert not db.check_access(1)
    with pytest.raises(ValueError):
        db.set_access_mode("invalid")


def test_manual_notification_cooldown(db):
    db.add_user(10, "user", "Roma")
    user = db.get_user(10)
    assert user is not None
    db.update_last_notification(10, is_manual=True)

    assert not db.can_send_manual_notification(10, cooldown_minutes=60)
    past = datetime.now(tz=ZoneInfo("UTC")) - timedelta(minutes=120)
    user.last_manual_notification = past
    db.session.commit()
    assert db.can_send_manual_notification(10, cooldown_minutes=60)


def test_format_last_notification(db):
    assert db.format_last_notification(999) == "Never"
    db.add_user(5, "echo", "Napoli")
    db.update_last_notification(5)
    formatted = db.format_last_notification(5)
    assert formatted != "Never"


def test_queue_message_lifecycle(db):
    assert db.queue_message(7, "hello")
    pending = db.get_pending_messages(limit=5)
    assert pending
    message_id = pending[0].id
    assert db.mark_message_sent(message_id)
    assert not db.get_pending_messages()


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeTelegramClient:
    def __init__(self, blocked_ids: set[int]):
        self.blocked_ids = blocked_ids
        self.deleted: list[tuple[int, int]] = []

    async def send_message(self, chat_id: int, text: str, disable_notification: bool):
        if chat_id in self.blocked_ids:
            raise Exception("Forbidden: user has blocked the bot")
        return SimpleNamespace(message_id=999)

    async def delete_message(self, chat_id: int, message_id: int):
        self.deleted.append((chat_id, message_id))


class FakeBot:
    def __init__(self, blocked_ids: set[int]):
        self.bot = FakeTelegramClient(blocked_ids)


def test_recheck_blocked_users(db):
    db.add_user(1, "alpha", "Roma")
    db.add_user(2, "beta", "Milano")
    db.mark_user_blocked(1)
    db.mark_user_blocked(2)
    fake_bot = FakeBot(blocked_ids={2})
    result = _run_async(db.recheck_blocked_users(fake_bot))
    assert result["checked"] == 2
    assert result["unblocked"] == 1
    assert result["still_blocked"] == 1
    assert result["errors"] == []
    assert not db.get_user(1).is_blocked
    assert db.get_user(2).is_blocked
    assert fake_bot.bot.deleted == [(1, 999)]


def test_recheck_blocked_users_reports_errors(db):
    class ErrorTelegramClient(FakeTelegramClient):
        async def send_message(self, chat_id: int, text: str, disable_notification: bool):
            raise RuntimeError("connection problem")

    class ErrorBot:
        def __init__(self):
            self.bot = ErrorTelegramClient(set())

    db.add_user(3, "delta", "Torino")
    db.mark_user_blocked(3)
    result = _run_async(db.recheck_blocked_users(ErrorBot()))
    assert result["checked"] == 1
    assert result["unblocked"] == 0
    assert result["still_blocked"] == 0
    assert result["errors"]
    user = db.get_user(3)
    assert user.last_block_status_check_at is not None
