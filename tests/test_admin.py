from base64 import b64encode

import pytest

import partita_bot.admin as admin_module
import partita_bot.config as config
from partita_bot.storage import AccessControl, Database


def auth_header() -> dict[str, str]:
    token = b64encode(f"{config.ADMIN_USERNAME}:{config.ADMIN_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def admin_test_env():
    original_db = admin_module.db
    test_db = Database(database_url="sqlite:///:memory:")
    admin_module.db = test_db
    admin_module.app.secret_key = "test-secret"
    yield admin_module, test_db
    admin_module.db = original_db
    test_db.close()


def test_notify_all_queues_admin_operation(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")
    db.add_user(2, "bob", "roma")
    db.set_user_cities(1, ["roma"])
    db.set_user_cities(2, ["roma"])

    with admin_app.app.test_client() as client:
        response = client.post("/notify_all", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    pending = db.get_pending_admin_operations(limit=10)
    assert len(pending) == 1
    assert pending[0].operation == "NOTIFY_ALL_USERS"


def test_notify_user_queues_admin_operation(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")
    db.set_user_cities(1, ["roma"])

    with admin_app.app.test_client() as client:
        response = client.post("/notify_user/1", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    pending = db.get_pending_admin_operations(limit=10)
    assert len(pending) == 1
    assert pending[0].operation == "NOTIFY_SINGLE_USER"
    assert pending[0].payload == "1"


def test_notify_user_respects_cooldown(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")
    db.set_user_cities(1, ["roma"])
    db.update_last_notification(1, is_manual=True)

    with admin_app.app.test_client() as client:
        response = client.post("/notify_user/1", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    pending = db.get_pending_messages()
    admin_ops = [msg for msg in pending if msg.telegram_id == 0]
    assert len(admin_ops) == 0


def test_notify_user_skips_no_cities(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")

    with admin_app.app.test_client() as client:
        response = client.post("/notify_user/1", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    pending = db.get_pending_messages()
    admin_ops = [msg for msg in pending if msg.telegram_id == 0]
    assert len(admin_ops) == 0


def test_notify_user_skips_nonexistent_user(admin_test_env):
    admin_app, db = admin_test_env

    with admin_app.app.test_client() as client:
        response = client.post("/notify_user/999", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    pending = db.get_pending_messages()
    admin_ops = [msg for msg in pending if msg.telegram_id == 0]
    assert len(admin_ops) == 0


def test_set_mode_switches_access(admin_test_env):
    admin_app, db = admin_test_env

    with admin_app.app.test_client() as client:
        response = client.post(
            "/set_mode",
            data={"mode": "whitelist"},
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert db.get_access_mode() == "whitelist"


def test_toggle_access_updates_lists(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(10, "lucy", "Torino")
    db.set_access_mode("whitelist")

    with admin_app.app.test_client() as client:
        client.post(
            "/toggle_access/10",
            data={"action": "allow"},
            headers=auth_header(),
            follow_redirects=True,
        )

    entries = db.session.query(AccessControl).filter_by(mode="whitelist", telegram_id=10).all()
    assert entries


def test_cleanup_users_queues_operation(admin_test_env):
    admin_app, db = admin_test_env

    with admin_app.app.test_client() as client:
        response = client.post("/cleanup_users", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    pending = db.get_pending_admin_operations(limit=10)
    assert len(pending) == 1
    assert pending[0].operation == "RECHECK_BLOCKED_USERS"


def test_test_notification_queues_message(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(2, "mike", "Milano")

    with admin_app.app.test_client() as client:
        response = client.post("/test_notification/2", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    queued = db.get_pending_messages()
    assert queued
    assert "Test notifiche eventi" in queued[0].message


def test_admin_index_shows_block_status(admin_test_env):
    admin_app, db = admin_test_env
    user = db.add_user(10, "blocked", "Roma")
    db.mark_user_blocked(user.telegram_id)

    with admin_app.app.test_client() as client:
        response = client.get("/", headers=auth_header())
        html = response.get_data(as_text=True)

    assert "Blocked" in html
    assert "Last Block Check" in html
    assert "Yes" in html


def test_send_custom_message_queues_message(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")

    with admin_app.app.test_client() as client:
        response = client.post(
            "/send_custom_message/1",
            data={"message_text": "Hello custom message!"},
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    queued = db.get_pending_messages()
    assert queued
    assert queued[0].message == "Hello custom message!"
    assert queued[0].telegram_id == 1


def test_send_custom_message_empty_text_fails(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")

    with admin_app.app.test_client() as client:
        response = client.post(
            "/send_custom_message/1",
            data={"message_text": "  "},
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    queued = db.get_pending_messages()
    assert len(queued) == 0


def test_send_custom_message_user_not_found(admin_test_env):
    admin_app, db = admin_test_env

    with admin_app.app.test_client() as client:
        response = client.post(
            "/send_custom_message/999",
            data={"message_text": "Hello!"},
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    queued = db.get_pending_messages()
    assert len(queued) == 0


def test_delete_user_pending_removes_recent_messages(admin_test_env):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")

    db.queue_message(1, "Old message 1")
    db.queue_message(1, "Old message 2")
    db.queue_message(1, "Recent message")
    messages = db.get_pending_messages(limit=10)
    old_time = datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=25)
    for msg in messages[:2]:
        msg.created_at = old_time
    db.session.commit()

    with admin_app.app.test_client() as client:
        response = client.post(
            "/delete_user_pending/1",
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    remaining = db.get_pending_messages(limit=10)
    assert len(remaining) == 2
    assert all("Old message" in msg.message for msg in remaining)


def test_delete_user_pending_no_messages(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")

    with admin_app.app.test_client() as client:
        response = client.post(
            "/delete_user_pending/1",
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    remaining = db.get_pending_messages(limit=10)
    assert len(remaining) == 0


def test_delete_user_pending_user_not_found(admin_test_env):
    admin_app, db = admin_test_env

    with admin_app.app.test_client() as client:
        response = client.post(
            "/delete_user_pending/999",
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200


def test_delete_user_sent_last_hour_queues_operation(admin_test_env):
    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")

    with admin_app.app.test_client() as client:
        response = client.post(
            "/delete_user_sent_last_hour/1",
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    pending = db.get_pending_admin_operations(limit=10)
    assert len(pending) == 1
    assert pending[0].operation == "DELETE_SENT_LAST_HOURS"
    assert pending[0].payload == "1:1"


def test_delete_user_sent_last_hour_user_not_found(admin_test_env):
    admin_app, db = admin_test_env

    with admin_app.app.test_client() as client:
        response = client.post(
            "/delete_user_sent_last_hour/999",
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    pending = db.get_pending_messages()
    admin_ops = [msg for msg in pending if msg.telegram_id == 0]
    assert len(admin_ops) == 0


def test_clear_classification_cache_clears_entries(admin_test_env):
    admin_app, db = admin_test_env
    db.set_city_classification("roma", True, "roma")
    db.set_city_classification("milano", True, "milano")

    with admin_app.app.test_client() as client:
        response = client.post(
            "/clear_classification_cache",
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    cached_is_city, _ = db.get_city_classification("roma")
    assert cached_is_city is None


def test_approve_pending_adds_to_whitelist_and_removes_pending(admin_test_env):
    admin_app, db = admin_test_env
    db.set_access_mode("whitelist")
    db.upsert_pending_request(12345, "testuser")

    with admin_app.app.test_client() as client:
        response = client.post(
            "/approve_pending/12345",
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert db.check_access(12345)
    assert len(db.list_pending_requests()) == 0


def test_dismiss_pending_removes_from_pending(admin_test_env):
    admin_app, db = admin_test_env
    db.set_access_mode("whitelist")
    db.upsert_pending_request(12345, "testuser")

    with admin_app.app.test_client() as client:
        response = client.post(
            "/dismiss_pending/12345",
            headers=auth_header(),
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert not db.check_access(12345)
    assert len(db.list_pending_requests()) == 0


def test_index_shows_pending_requests_in_whitelist_mode(admin_test_env):
    admin_app, db = admin_test_env
    db.set_access_mode("whitelist")
    db.upsert_pending_request(111, "user1")
    db.upsert_pending_request(222, "user2")

    with admin_app.app.test_client() as client:
        response = client.get("/", headers=auth_header())
        html = response.get_data(as_text=True)

    assert "Pending Whitelist Requests" in html
    assert "111" in html
    assert "222" in html
    assert "user1" in html
    assert "user2" in html


def test_index_no_pending_requests_in_blocklist_mode(admin_test_env):
    admin_app, db = admin_test_env
    db.set_access_mode("blocklist")
    db.upsert_pending_request(111, "user1")

    with admin_app.app.test_client() as client:
        response = client.get("/", headers=auth_header())
        html = response.get_data(as_text=True)

    assert "Pending Whitelist Requests" not in html


def test_clear_event_cache_clears_today_for_all_cities(admin_test_env):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    admin_app, db = admin_test_env
    db.add_user(1, "alice", "Roma")
    db.add_user(2, "bob", "Milano")
    db.set_user_cities(1, ["roma"])
    db.set_user_cities(2, ["milano"])

    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    db.save_event_cache("roma", today, "yes", [{"title": "Test"}])
    db.save_event_cache("milano", today, "yes", [{"title": "Test"}])

    with admin_app.app.test_client() as client:
        response = client.post("/clear_event_cache", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    cached_roma = db.get_event_cache("roma", today)
    cached_milano = db.get_event_cache("milano", today)
    assert cached_roma is None
    assert cached_milano is None
