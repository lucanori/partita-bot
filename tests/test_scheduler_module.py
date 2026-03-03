from datetime import datetime
from zoneinfo import ZoneInfo

import partita_bot.scheduler as scheduler


def test_create_scheduler_uses_background_scheduler(monkeypatch):
    created: list[scheduler.BackgroundScheduler] = []

    class DummyScheduler:
        def __init__(self, timezone, job_defaults):
            self.timezone = timezone
            self.job_defaults = job_defaults
            self.jobs: list[dict[str, object]] = []
            self.started = False
            self.stopped = False
            created.append(self)

        def add_job(self, func, trigger, **kwargs):
            self.jobs.append({"func": func, "trigger": trigger, **kwargs})

        def start(self):
            self.started = True

        def shutdown(self):
            self.stopped = True

    monkeypatch.setattr(scheduler, "BackgroundScheduler", DummyScheduler)

    match_scheduler = scheduler.create_scheduler()

    assert isinstance(match_scheduler, scheduler.MatchScheduler)
    dummy = created[0]
    assert dummy.jobs
    assert dummy.jobs[0]["id"] == "morning_notifications"

    match_scheduler.start()
    assert dummy.started

    match_scheduler.stop()
    assert dummy.stopped


def test_match_scheduler_start_stop_delegate():
    class SpyScheduler:
        def __init__(self):
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def shutdown(self):
            self.stopped = True

    spy = SpyScheduler()
    match_scheduler = scheduler.MatchScheduler(spy)
    match_scheduler.start()
    assert spy.started
    match_scheduler.stop()
    assert spy.stopped


class DummyScheduler:
    def __init__(self, timezone, job_defaults):
        self.timezone = timezone
        self.job_defaults = job_defaults
        self.jobs: list[dict[str, object]] = []
        self.started = False
        self.stopped = False

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def start(self):
        self.started = True

    def shutdown(self):
        self.stopped = True


class StubDatabase:
    def __init__(self, last_run=None):
        self.last_run = last_run
        self.updated = False
        self.queue_message = lambda *_, **__: None

    def get_scheduler_last_run(self):
        return self.last_run

    def get_all_users(self):
        return []

    def update_scheduler_last_run(self):
        self.updated = True


def _patch_datetime(monkeypatch, fixed_now):
    class FixedDatetime(datetime):
        _now = fixed_now

        @classmethod
        def now(cls, tz=None):
            if tz:
                return cls._now.astimezone(tz)
            return cls._now

    monkeypatch.setattr(scheduler, "datetime", FixedDatetime)


def _setup_scheduler(monkeypatch, fixed_now, last_run=None):
    schedulers: list[DummyScheduler] = []
    stub_db = StubDatabase(last_run=last_run)

    def background_scheduler(timezone, job_defaults):
        stub = DummyScheduler(timezone, job_defaults)
        schedulers.append(stub)
        return stub

    monkeypatch.setattr(scheduler, "BackgroundScheduler", background_scheduler)
    monkeypatch.setattr(scheduler, "Database", lambda: stub_db)
    monkeypatch.setattr(scheduler, "EventFetcher", lambda db: object())

    summary_calls: list[datetime] = []

    def fake_process_notifications(*args, **kwargs):
        summary_calls.append(kwargs["local_time"])
        return {"notifications_sent": 1, "no_events": 0, "already_notified": 0}

    monkeypatch.setattr(scheduler, "process_notifications", fake_process_notifications)
    _patch_datetime(monkeypatch, fixed_now)

    match_scheduler = scheduler.create_scheduler()
    stub_scheduler = schedulers[0]
    return match_scheduler, stub_scheduler, stub_db, summary_calls


def test_scheduler_runs_notifications_within_window(monkeypatch):
    fixed_now = datetime(2026, 3, 2, 8, 30, tzinfo=ZoneInfo("UTC"))
    _, stub_scheduler, stub_db, summary_calls = _setup_scheduler(monkeypatch, fixed_now)
    job_func = stub_scheduler.jobs[0]["func"]
    job_func()
    assert summary_calls
    assert stub_db.updated


def test_scheduler_skips_outside_window(monkeypatch):
    fixed_now = datetime(2026, 3, 2, 5, 0, tzinfo=ZoneInfo("UTC"))
    _, stub_scheduler, stub_db, summary_calls = _setup_scheduler(monkeypatch, fixed_now)
    job_func = stub_scheduler.jobs[0]["func"]
    job_func()
    assert not summary_calls
    assert not stub_db.updated


def test_scheduler_skips_if_already_run_today(monkeypatch):
    fixed_now = datetime(2026, 3, 2, 8, 30, tzinfo=ZoneInfo("UTC"))
    _, stub_scheduler, stub_db, summary_calls = _setup_scheduler(
        monkeypatch, fixed_now, last_run=fixed_now
    )
    job_func = stub_scheduler.jobs[0]["func"]
    job_func()
    assert not summary_calls
    assert not stub_db.updated
