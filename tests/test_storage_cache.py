from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from partita_bot.storage import Database


def test_event_cache_roundtrip_is_normalized():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        db.save_event_cache(
            city="Roma",
            target_date=today,
            status="yes",
            events=[{"title": "Test Event", "time": "18:00"}],
        )

        cached = db.get_event_cache("roma", today)
        assert cached is not None
        cached_data = cached
        assert cached_data["status"] == "yes"
        assert cached_data["events"][0]["title"] == "Test Event"

        db.save_event_cache("ROMA", today, status="no", events=[])
        cached = db.get_event_cache("Roma", today)
        assert cached is not None
        cached_data = cached
        assert cached_data["status"] == "no"
        assert cached_data["events"] == []


def test_delete_event_cache_removes_entry():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        db.save_event_cache("roma", today, "yes", [{"title": "Test"}])
        cached = db.get_event_cache("roma", today)
        assert cached is not None

        deleted = db.delete_event_cache("roma", today)
        assert deleted == 1

        cached = db.get_event_cache("roma", today)
        assert cached is None


def test_delete_event_cache_no_match_returns_zero():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()
        yesterday = today - timedelta(days=1)

        db.save_event_cache("roma", today, "yes", [{"title": "Test"}])

        deleted = db.delete_event_cache("roma", yesterday)
        assert deleted == 0

        cached = db.get_event_cache("roma", today)
        assert cached is not None


def test_delete_event_cache_normalizes_city():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        db.save_event_cache("Roma", today, "yes", [{"title": "Test"}])

        deleted = db.delete_event_cache("ROMA", today)
        assert deleted == 1

        cached = db.get_event_cache("roma", today)
        assert cached is None


def test_delete_event_cache_empty_city_returns_zero():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        deleted = db.delete_event_cache("", today)
        assert deleted == 0


def test_get_all_cities_with_users_returns_distinct_cities():
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.add_user(2, "bob", "Milano")
        db.add_user(3, "charlie", "Roma")
        db.set_user_cities(1, ["roma"])
        db.set_user_cities(2, ["milano"])
        db.set_user_cities(3, ["roma"])

        cities = db.get_all_cities_with_users()
        assert sorted(cities) == ["milano", "roma"]
