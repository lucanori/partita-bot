from datetime import datetime
from zoneinfo import ZoneInfo

from partita_bot.notifications import process_notifications
from partita_bot.scheduler import calculate_next_run
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
        db.set_user_cities(1, ["roma"])
        db.set_user_cities(2, ["roma"])
        db.set_user_cities(3, ["milano"])

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
        db.set_user_cities(1, ["roma"])
        db.set_user_cities(2, ["milano"])

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


def test_process_notifications_skips_blocked_users():
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.add_user(2, "bob", "Milano")
        db.set_user_cities(1, ["roma"])
        db.set_user_cities(2, ["milano"])
        db.mark_user_blocked(1)

        fetcher = DummyFetcher()
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["notifications_sent"] == 1
        queued = db.get_pending_messages()
        assert len(queued) == 1


def test_process_notifications_skips_users_without_cities():
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.add_user(2, "bob", "Milano")

        fetcher = DummyFetcher()
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["notifications_sent"] == 0
        assert summary["no_events"] == 0


def test_process_notifications_skips_access_blocked_users():
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.add_user(2, "bob", "Milano")
        db.set_user_cities(1, ["roma"])
        db.set_user_cities(2, ["milano"])
        db.add_to_list("blocklist", 1)

        fetcher = DummyFetcher()
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["notifications_sent"] == 1
        queued = db.get_pending_messages()
        assert len(queued) == 1
        assert queued[0].telegram_id == 2


def test_process_notifications_notifies_once_per_user_multiple_cities():
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma", "milano"])

        fetcher = DummyFetcher()
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["notifications_sent"] == 1
        assert len(fetcher.calls) == 2
        queued = db.get_pending_messages()
        assert len(queued) == 1


def test_calculate_next_run_before_window_schedules_today():
    current_utc = datetime(2026, 3, 4, 2, 12, tzinfo=ZoneInfo("UTC"))
    start_hour = 8
    timezone = ZoneInfo("UTC")

    result = calculate_next_run(
        current_utc=current_utc,
        start_hour=start_hour,
        timezone=timezone,
    )

    expected = datetime(2026, 3, 4, 8, 0, tzinfo=ZoneInfo("UTC"))
    assert result == expected


def test_calculate_next_run_during_window_schedules_next_day():
    current_utc = datetime(2026, 3, 4, 8, 30, tzinfo=ZoneInfo("UTC"))
    start_hour = 8
    timezone = ZoneInfo("UTC")

    result = calculate_next_run(
        current_utc=current_utc,
        start_hour=start_hour,
        timezone=timezone,
    )

    expected = datetime(2026, 3, 5, 8, 0, tzinfo=ZoneInfo("UTC"))
    assert result == expected


def test_calculate_next_run_after_window_schedules_next_day():
    current_utc = datetime(2026, 3, 4, 11, 0, tzinfo=ZoneInfo("UTC"))
    start_hour = 8
    timezone = ZoneInfo("UTC")

    result = calculate_next_run(
        current_utc=current_utc,
        start_hour=start_hour,
        timezone=timezone,
    )

    expected = datetime(2026, 3, 5, 8, 0, tzinfo=ZoneInfo("UTC"))
    assert result == expected


def test_calculate_next_run_at_exact_start_schedules_next_day():
    current_utc = datetime(2026, 3, 4, 8, 0, tzinfo=ZoneInfo("UTC"))
    start_hour = 8
    timezone = ZoneInfo("UTC")

    result = calculate_next_run(
        current_utc=current_utc,
        start_hour=start_hour,
        timezone=timezone,
    )

    expected = datetime(2026, 3, 5, 8, 0, tzinfo=ZoneInfo("UTC"))
    assert result == expected
