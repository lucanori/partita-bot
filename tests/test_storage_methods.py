import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import partita_bot.config as config
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


def test_format_last_notification_respects_config_timezone(db):
    original_tz = config.TIMEZONE
    original_tz_info = config.TIMEZONE_INFO
    try:
        config.set_timezone("America/New_York")
        db.add_user(99, "timezone_test", "Roma")
        utc_time = datetime(2026, 3, 6, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        user = db.get_user(99)
        user.last_notification = utc_time
        db.session.commit()
        formatted = db.format_last_notification(99)
        assert "2026-03-06" in formatted
        assert "07:00:00" in formatted
    finally:
        config.TIMEZONE = original_tz
        config.TIMEZONE_INFO = original_tz_info


def test_format_datetime_respects_config_timezone(db):
    original_tz = config.TIMEZONE
    original_tz_info = config.TIMEZONE_INFO
    try:
        config.set_timezone("Asia/Tokyo")
        utc_time = datetime(2026, 3, 6, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        formatted = db.format_datetime(utc_time)
        assert "2026-03-06" in formatted
        assert "21:00:00" in formatted
    finally:
        config.TIMEZONE = original_tz
        config.TIMEZONE_INFO = original_tz_info


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


def test_delete_pending_messages_older_than_purges_old_and_keeps_recent(db):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    db.queue_message(1, "Message 1")
    db.queue_message(2, "Message 2")
    db.queue_message(3, "Message 3")
    messages = db.get_pending_messages(limit=10)
    old_time = datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=25)
    for msg in messages[:2]:
        msg.created_at = old_time
    db.session.commit()

    deleted = db.delete_pending_messages_older_than(hours=24)
    assert deleted == 2

    remaining = db.get_pending_messages(limit=10)
    assert len(remaining) == 1
    assert remaining[0].telegram_id == 3


def test_delete_pending_messages_for_user_last_n_hours(db):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    db.queue_message(1, "User 1 Old Message")
    db.queue_message(1, "User 1 Recent Message")
    db.queue_message(2, "User 2 Old Message")
    db.queue_message(2, "User 2 Recent Message")
    messages = db.get_pending_messages(limit=10)
    old_time = datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=25)
    messages[0].created_at = old_time
    messages[2].created_at = old_time
    db.session.commit()

    deleted = db.delete_pending_messages_for_user_last_n_hours(1, hours=24)
    assert deleted == 1

    remaining = db.get_pending_messages(limit=10)
    assert len(remaining) == 3
    assert all(msg.telegram_id == 2 or "Old" in msg.message for msg in remaining)


def test_mark_message_sent_stores_sent_message_id(db):
    from partita_bot.storage import MessageQueue

    db.queue_message(1, "Test message")
    pending = db.get_pending_messages(limit=5)
    assert pending
    message_id = pending[0].id

    assert db.mark_message_sent(message_id, sent_message_id=12345)

    msg = db.session.query(MessageQueue).filter_by(id=message_id).first()
    assert msg.sent is True
    assert msg.sent_message_id == 12345
    assert msg.sent_at is not None


def test_get_sent_messages_for_user_within_hours(db):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    db.queue_message(1, "Recent sent message")
    db.queue_message(1, "Old sent message")
    db.queue_message(1, "Recent sent no id")
    db.queue_message(2, "Other user message")

    pending = db.get_pending_messages(limit=10)
    assert len(pending) == 4

    db.mark_message_sent(pending[0].id, sent_message_id=100)
    db.mark_message_sent(pending[1].id, sent_message_id=101)
    db.mark_message_sent(pending[2].id, sent_message_id=None)
    db.mark_message_sent(pending[3].id, sent_message_id=102)
    old_time = datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=2)
    pending[1].sent_at = old_time
    db.session.commit()

    sent_messages = db.get_sent_messages_for_user_within_hours(1, hours=1)

    assert len(sent_messages) == 1
    assert sent_messages[0].sent_message_id == 100
    assert "Recent sent message" in sent_messages[0].message


def test_get_sent_messages_for_user_respects_limit(db):
    for i in range(10):
        db.queue_message(1, f"Message {i}")

    pending = db.get_pending_messages(limit=20)
    for msg in pending:
        db.mark_message_sent(msg.id, sent_message_id=100 + msg.id)

    sent_messages = db.get_sent_messages_for_user_within_hours(1, hours=1, limit=5)
    assert len(sent_messages) == 5


def test_delete_sent_messages_for_user_within_hours(db):
    db.queue_message(1, "Message to delete 1")
    db.queue_message(1, "Message to delete 2")

    pending = db.get_pending_messages(limit=10)
    db.mark_message_sent(pending[0].id, sent_message_id=1001)
    db.mark_message_sent(pending[1].id, sent_message_id=1002)

    deleted_messages: list[tuple[int, int]] = []

    class FakeTelegramClient:
        async def delete_message(self, chat_id: int, message_id: int):
            if message_id == 1002:
                raise Exception("Message already deleted")
            deleted_messages.append((chat_id, message_id))

    class FakeBot:
        def __init__(self):
            self.bot = FakeTelegramClient()

    fake_bot = FakeBot()

    result = _run_async(db.delete_sent_messages_for_user_within_hours(fake_bot, 1, hours=1))

    assert result["success_count"] == 1
    assert result["error_count"] == 1
    assert result["total_attempted"] == 2
    assert len(result["errors"]) == 1
    assert deleted_messages == [(1, 1001)]


def test_upsert_pending_request_creates_new(db):
    db.upsert_pending_request(12345, "testuser")
    pending = db.list_pending_requests()
    assert len(pending) == 1
    assert pending[0].telegram_id == 12345
    assert pending[0].username == "testuser"
    assert pending[0].first_seen is not None
    assert pending[0].last_seen is not None


def test_upsert_pending_request_updates_existing(db):
    db.upsert_pending_request(12345, "olduser")
    first_seen = db.list_pending_requests()[0].first_seen
    db.upsert_pending_request(12345, "newuser")
    pending = db.list_pending_requests()
    assert len(pending) == 1
    assert pending[0].telegram_id == 12345
    assert pending[0].username == "newuser"
    assert pending[0].first_seen == first_seen
    assert pending[0].last_seen > first_seen


def test_remove_pending_request(db):
    db.upsert_pending_request(12345, "testuser")
    assert len(db.list_pending_requests()) == 1
    result = db.remove_pending_request(12345)
    assert result is True
    assert len(db.list_pending_requests()) == 0


def test_remove_pending_request_nonexistent(db):
    result = db.remove_pending_request(99999)
    assert result is False


def test_list_pending_requests_ordered_by_first_seen(db):
    db.upsert_pending_request(3, "user3")
    db.upsert_pending_request(1, "user1")
    db.upsert_pending_request(2, "user2")
    pending = db.list_pending_requests()
    assert len(pending) == 3
    assert pending[0].telegram_id == 3
    assert pending[1].telegram_id == 1
    assert pending[2].telegram_id == 2


def test_should_send_denial_first_time_sends(db):
    result = db.should_send_denial(99999, cooldown_seconds=300)
    assert result is True


def test_should_send_denial_within_cooldown_suppresses(db):
    user_id = 88888
    db.should_send_denial(user_id, cooldown_seconds=300)
    result = db.should_send_denial(user_id, cooldown_seconds=300)
    assert result is False


def test_should_send_denial_after_cooldown_sends(db):
    from partita_bot.storage import AccessDenialLog

    user_id = 77777
    db.should_send_denial(user_id, cooldown_seconds=300)
    entry = db.session.query(AccessDenialLog).filter_by(telegram_id=user_id).first()
    past = datetime.now(tz=ZoneInfo("UTC")) - timedelta(seconds=301)
    entry.last_sent = past
    db.session.commit()
    result = db.should_send_denial(user_id, cooldown_seconds=300)
    assert result is True


def test_should_send_denial_small_cooldown(db):
    user_id = 66666
    db.should_send_denial(user_id, cooldown_seconds=1)
    import time

    time.sleep(1.1)
    result = db.should_send_denial(user_id, cooldown_seconds=1)
    assert result is True


def test_enqueue_admin_operation_uses_admin_queue(db):
    result = db.enqueue_admin_operation("NOTIFY_ALL_USERS")
    assert result is True

    pending = db.get_pending_admin_operations(limit=10)
    assert len(pending) == 1
    assert pending[0].operation == "NOTIFY_ALL_USERS"
    assert pending[0].payload is None
    assert pending[0].processed is False


def test_enqueue_admin_operation_with_params(db):
    result = db.enqueue_admin_operation("NOTIFY_SINGLE_USER", ["123", "param2"])
    assert result is True

    pending = db.get_pending_admin_operations(limit=10)
    assert len(pending) == 1
    assert pending[0].operation == "NOTIFY_SINGLE_USER"
    assert pending[0].payload == "123:param2"


def test_mark_admin_operation_processed(db):
    from partita_bot.storage import AdminQueue

    db.enqueue_admin_operation("NOTIFY_ALL_USERS")

    pending = db.get_pending_admin_operations(limit=10)
    op_id = pending[0].id

    result = db.mark_admin_operation_processed(op_id)
    assert result is True

    pending_after = db.get_pending_admin_operations(limit=10)
    assert len(pending_after) == 0

    operation = db.session.query(AdminQueue).filter_by(id=op_id).first()
    assert operation.processed is True
    assert operation.processed_at is not None


def test_mark_admin_operation_processed_invalid_id(db):
    result = db.mark_admin_operation_processed(99999)
    assert result is False


def test_get_pending_admin_operations_respects_limit(db):
    for i in range(15):
        db.enqueue_admin_operation(f"OP_{i}")

    pending = db.get_pending_admin_operations(limit=10)
    assert len(pending) == 10


def test_get_pending_admin_operations_orders_by_created_at(db):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    db.enqueue_admin_operation("FIRST")

    first_op = db.get_pending_admin_operations(limit=1)[0]
    first_op.created_at = datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=1)
    db.session.commit()

    db.enqueue_admin_operation("SECOND")

    pending = db.get_pending_admin_operations(limit=10)
    assert len(pending) == 2
    assert pending[0].operation == "FIRST"
    assert pending[1].operation == "SECOND"
