from datetime import date, datetime
from zoneinfo import ZoneInfo

from partita_bot.notifications import process_notifications
from partita_bot.storage import Database


class MultiCityFetcher:
    def __init__(self, city_responses: dict[str, str | None]):
        self.city_responses = city_responses
        self.calls: list[str] = []

    def fetch_event_message(self, city: str, target_date: date) -> str | None:
        self.calls.append(city)
        return self.city_responses.get(city)


def test_process_notifications_multi_city_onboarding_no_events_then_events():
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Parma")
        db.set_user_cities(1, ["parma", "milano"])

        fetcher = MultiCityFetcher(city_responses={"Parma": None, "Milano": "Evento a Milano"})
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["notifications_sent"] == 1
        assert summary["no_events"] == 1
        assert summary["already_notified"] == 0

        queued = db.get_pending_messages()
        assert len(queued) == 1
        assert queued[0].telegram_id == 1
        assert queued[0].message == "Evento a Milano"

        assert fetcher.calls == ["Parma", "Milano"]
