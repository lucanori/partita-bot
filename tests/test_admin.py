from base64 import b64encode

import pytest

import partita_bot.admin as admin_module
import partita_bot.config as config
from partita_bot.storage import AccessControl, Database


def auth_header() -> dict[str, str]:
    token = b64encode(f"{config.ADMIN_USERNAME}:{config.ADMIN_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class DummyFetcher:
    def __init__(self):
        self.calls: list[str] = []

    def fetch_event_message(self, city: str, target_date) -> str:
        self.calls.append(city)
        return f"Evento per {city}"


@pytest.fixture
def admin_test_env():
    original_db = admin_module.db
    original_fetcher = admin_module.event_fetcher
    test_db = Database(database_url="sqlite:///:memory:")
    dummy_fetcher = DummyFetcher()
    admin_module.db = test_db
    admin_module.event_fetcher = dummy_fetcher
    admin_module.app.secret_key = "test-secret"
    yield admin_module, test_db, dummy_fetcher
    admin_module.db = original_db
    admin_module.event_fetcher = original_fetcher
    test_db.close()


def test_notify_all_groups_by_city(admin_test_env):
    admin_app, db, fetcher = admin_test_env
    db.add_user(1, "alice", "Roma")
    db.add_user(2, "bob", "roma")

    with admin_app.app.test_client() as client:
        response = client.post("/notify_all", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    assert len(fetcher.calls) == 1
    assert len(db.get_pending_messages()) == 2
    assert db.get_user(1).last_notification is not None


def test_notify_user_manual_trigger(admin_test_env):
    admin_app, db, fetcher = admin_test_env
    db.add_user(1, "alice", "Roma")

    with admin_app.app.test_client() as client:
        response = client.post("/notify_user/1", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    assert fetcher.calls == ["Roma"]
    user = db.get_user(1)
    assert user is not None
    assert user.last_manual_notification is not None


def test_set_mode_switches_access(admin_test_env):
    admin_app, db, _ = admin_test_env

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
    admin_app, db, _ = admin_test_env
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
    admin_app, db, _ = admin_test_env

    with admin_app.app.test_client() as client:
        response = client.post("/cleanup_users", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    pending = db.get_pending_messages()
    assert any("ADMIN_OPERATION:RECHECK_BLOCKED_USERS" in item.message for item in pending)


def test_test_notification_queues_message(admin_test_env):
    admin_app, db, _ = admin_test_env
    db.add_user(2, "mike", "Milano")

    with admin_app.app.test_client() as client:
        response = client.post("/test_notification/2", headers=auth_header(), follow_redirects=True)
        assert response.status_code == 200

    queued = db.get_pending_messages()
    assert queued
    assert "Test notifiche eventi" in queued[0].message


def test_admin_index_shows_block_status(admin_test_env):
    admin_app, db, _ = admin_test_env
    user = db.add_user(10, "blocked", "Roma")
    db.mark_user_blocked(user.telegram_id)

    with admin_app.app.test_client() as client:
        response = client.get("/", headers=auth_header())
        html = response.get_data(as_text=True)

    assert "Blocked" in html
    assert "Last Block Check" in html
    assert "Yes" in html
