from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from partita_bot.event_fetcher import QUERY_TYPE_FOOTBALL, QUERY_TYPE_GENERAL
from partita_bot.storage import Database


def test_event_cache_roundtrip_is_normalized():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        db.save_event_cache(
            city="Roma",
            target_date=today,
            status="yes",
            events=[{"title": "Test Event", "time": "18:00"}],
            query_type=QUERY_TYPE_GENERAL,
        )

        cached = db.get_event_cache("roma", today, QUERY_TYPE_GENERAL)
        assert cached is not None
        cached_data = cached
        assert cached_data["status"] == "yes"
        assert cached_data["events"][0]["title"] == "Test Event"

        db.save_event_cache("ROMA", today, status="no", events=[], query_type=QUERY_TYPE_GENERAL)
        cached = db.get_event_cache("Roma", today, QUERY_TYPE_GENERAL)
        assert cached is not None
        cached_data = cached
        assert cached_data["status"] == "no"
        assert cached_data["events"] == []


def test_event_cache_query_type_isolation():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        db.save_event_cache(
            city="Roma",
            target_date=today,
            status="yes",
            events=[{"title": "Football Match", "time": "20:00"}],
            query_type=QUERY_TYPE_FOOTBALL,
        )
        db.save_event_cache(
            city="Roma",
            target_date=today,
            status="yes",
            events=[{"title": "Concert", "time": "21:00"}],
            query_type=QUERY_TYPE_GENERAL,
        )

        cached_football = db.get_event_cache("roma", today, QUERY_TYPE_FOOTBALL)
        assert cached_football is not None
        assert cached_football["events"][0]["title"] == "Football Match"

        cached_general = db.get_event_cache("roma", today, QUERY_TYPE_GENERAL)
        assert cached_general is not None
        assert cached_general["events"][0]["title"] == "Concert"


def test_delete_event_cache_removes_all_query_types():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        db.save_event_cache("roma", today, "yes", [{"title": "Football"}], QUERY_TYPE_FOOTBALL)
        db.save_event_cache("roma", today, "yes", [{"title": "Concert"}], QUERY_TYPE_GENERAL)

        cached_football = db.get_event_cache("roma", today, QUERY_TYPE_FOOTBALL)
        assert cached_football is not None
        cached_general = db.get_event_cache("roma", today, QUERY_TYPE_GENERAL)
        assert cached_general is not None

        deleted = db.delete_event_cache("roma", today)
        assert deleted == 2

        cached_football = db.get_event_cache("roma", today, QUERY_TYPE_FOOTBALL)
        assert cached_football is None
        cached_general = db.get_event_cache("roma", today, QUERY_TYPE_GENERAL)
        assert cached_general is None


def test_delete_event_cache_no_match_returns_zero():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()
        yesterday = today - timedelta(days=1)

        db.save_event_cache("roma", today, "yes", [{"title": "Test"}], QUERY_TYPE_GENERAL)

        deleted = db.delete_event_cache("roma", yesterday)
        assert deleted == 0

        cached = db.get_event_cache("roma", today, QUERY_TYPE_GENERAL)
        assert cached is not None


def test_delete_event_cache_normalizes_city():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        db.save_event_cache("Roma", today, "yes", [{"title": "Test"}], QUERY_TYPE_GENERAL)

        deleted = db.delete_event_cache("ROMA", today)
        assert deleted == 1

        cached = db.get_event_cache("roma", today, QUERY_TYPE_GENERAL)
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


def test_save_event_cache_default_query_type():
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
        assert cached["status"] == "yes"


def test_get_event_cache_default_query_type():
    with Database(database_url="sqlite:///:memory:") as db:
        today = datetime.now(ZoneInfo("Europe/Rome")).date()

        db.save_event_cache(
            city="Roma",
            target_date=today,
            status="yes",
            events=[{"title": "Test Event", "time": "18:00"}],
            query_type="general",
        )

        cached = db.get_event_cache("roma", today)
        assert cached is not None
        assert cached["status"] == "yes"
