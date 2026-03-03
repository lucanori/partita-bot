from datetime import datetime
from zoneinfo import ZoneInfo

from partita_bot.notifications import process_notifications
from partita_bot.storage import Database


class DummyFetcher:
    def __init__(self):
        self.calls: list[str] = []

    def fetch_event_message(self, city: str, target_date) -> str:
        self.calls.append(city)
        return f"Evento per {city}"


def test_process_notifications_groups_by_city_once():
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.add_user(2, "bob", "roma")
        db.add_user(3, "carla", "Milano")

        fetcher = DummyFetcher()
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["notifications_sent"] == 3
        assert len(fetcher.calls) == 2


def test_process_notifications_skips_already_notified():
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.add_user(2, "bob", "Milano")

        first_user = db.get_user(1)
        first_user.last_notification = datetime(2026, 3, 2, 6, tzinfo=ZoneInfo("UTC"))
        db.session.commit()

        fetcher = DummyFetcher()
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["already_notified"] == 1
        assert summary["notifications_sent"] == 1
        assert summary["no_events"] == 0
