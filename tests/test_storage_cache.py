from datetime import datetime
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

        # Updating the same city/date replaces the previous response
        db.save_event_cache("ROMA", today, status="no", events=[])
        cached = db.get_event_cache("Roma", today)
        assert cached is not None
        cached_data = cached
        assert cached_data["status"] == "no"
        assert cached_data["events"] == []
