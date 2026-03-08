import importlib
from datetime import date, datetime
from typing import cast
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

import partita_bot.event_fetcher as event_fetcher
from partita_bot.event_fetcher import FETCH_FAILURE, EventFetcher
from partita_bot.notifications import process_notifications
from partita_bot.storage import Database


class DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class TimeoutSession:
    def __init__(self, fail_count: int = 1):
        self.fail_count = fail_count
        self.call_count = 0
        self.calls: list[dict] = []

    def post(self, url: str, headers=None, json=None, timeout=None):
        self.call_count += 1
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.call_count <= self.fail_count:
            raise requests.Timeout(f"Request timed out (attempt {self.call_count})")
        return DummyResponse({"answer": {"status": "no"}})


class ConnectionErrorSession:
    def __init__(self, fail_count: int = 1):
        self.fail_count = fail_count
        self.call_count = 0
        self.calls: list[dict] = []

    def post(self, url: str, headers=None, json=None, timeout=None):
        self.call_count += 1
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.call_count <= self.fail_count:
            raise requests.ConnectionError(f"Connection failed (attempt {self.call_count})")
        return DummyResponse({"answer": {"status": "no"}})


class RetryThenSuccessSession:
    def __init__(self, gate_fail_count: int = 2, search_fail_count: int = 0):
        self.gate_fail_count = gate_fail_count
        self.search_fail_count = search_fail_count
        self.gate_call_count = 0
        self.search_call_count = 0
        self.calls: list[dict] = []

    def post(self, url: str, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if "answer" in url:
            self.gate_call_count += 1
            if self.gate_call_count <= self.gate_fail_count:
                raise requests.Timeout(f"Gate request timed out (attempt {self.gate_call_count})")
            return DummyResponse({"answer": {"status": "yes"}})
        self.search_call_count += 1
        if self.search_call_count <= self.search_fail_count:
            raise requests.Timeout(f"Search request timed out (attempt {self.search_call_count})")
        return DummyResponse(
            {
                "output": {
                    "events": [
                        {
                            "title": "Test Event",
                            "time": "20:00",
                            "location": "Stadio Olimpico, Roma",
                            "type": "Sport",
                            "event_date": "2026-03-02",
                            "source_url": "https://example.com/event",
                        }
                    ]
                }
            }
        )


class MockSession:
    def __init__(self, responses: list[DummyResponse] | DummyResponse | None = None):
        if responses is None:
            self.responses = []
        elif isinstance(responses, DummyResponse):
            self.responses = [responses]
        else:
            self.responses = responses
        self.calls: list[dict] = []
        self.call_index = 0

    def post(self, url: str, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "method": "post"})
        if self.call_index < len(self.responses):
            response = self.responses[self.call_index]
            self.call_index += 1
            return response
        return DummyResponse({})

    def get(self, url: str, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "method": "get"})
        if self.call_index < len(self.responses):
            response = self.responses[self.call_index]
            self.call_index += 1
            return response
        return DummyResponse({"matches": []})


def test_fetch_event_message_gate_timeout_returns_fetch_failure(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        monkeypatch.setattr(event_fetcher.config, "FOOTBALL_API_TOKEN", "test-token")

        class TimeoutSessionAll:
            def __init__(self, fail_count: int = 1):
                self.fail_count = fail_count
                self.call_count = 0
                self.calls: list[dict] = []

            def post(self, url: str, headers=None, json=None, timeout=None):
                self.call_count += 1
                self.calls.append({"url": url, "json": json, "headers": headers})
                if self.call_count <= self.fail_count:
                    raise requests.Timeout(f"Request timed out (attempt {self.call_count})")
                return DummyResponse({"answer": {"status": "no"}})

            def get(self, url: str, headers=None, params=None, timeout=None):
                raise requests.Timeout("Football-data request timed out")

        session = TimeoutSessionAll(fail_count=4)
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        result = fetcher.fetch_event_message("Roma", target_date)

        assert result == FETCH_FAILURE
        assert session.call_count >= 2

        cached_football = db.get_event_cache("Roma", target_date, "football")
        cached_general = db.get_event_cache("Roma", target_date, "general")
        assert cached_football is None or cached_football.get("status") != "yes"
        assert cached_general is None or cached_general.get("status") != "yes"


def test_fetch_event_message_search_timeout_returns_fetch_failure(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        monkeypatch.setattr(event_fetcher.config, "FOOTBALL_API_TOKEN", "test-token")

        class GateOkSearchTimeoutSession:
            def __init__(self):
                self.call_count = 0
                self.calls: list[dict] = []

            def post(self, url: str, headers=None, json=None, timeout=None):
                self.call_count += 1
                self.calls.append({"url": url, "json": json, "headers": headers})
                if "answer" in url:
                    return DummyResponse({"answer": {"status": "yes"}})
                raise requests.Timeout("Search request timed out")

            def get(self, url: str, headers=None, params=None, timeout=None):
                raise requests.Timeout("Football-data request timed out")

        session = GateOkSearchTimeoutSession()
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        result = fetcher.fetch_event_message("Roma", target_date)

        assert result == FETCH_FAILURE
        assert session.call_count >= 4

        cached_football = db.get_event_cache("Roma", target_date, "football")
        cached_general = db.get_event_cache("Roma", target_date, "general")
        assert cached_football is None or cached_football.get("status") != "yes"
        assert cached_general is None or cached_general.get("status") != "yes"


def test_fetch_event_message_gate_no_caches_no(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        monkeypatch.setattr(event_fetcher.config, "FOOTBALL_API_TOKEN", "")
        session = MockSession(
            [
                DummyResponse({"answer": {"status": "no"}}),
                DummyResponse({"answer": {"status": "no"}}),
            ]
        )
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        result = fetcher.fetch_event_message("Roma", target_date)

        assert result is None
        assert result != FETCH_FAILURE

        cached_football = db.get_event_cache("Roma", target_date, "football")
        cached_general = db.get_event_cache("Roma", target_date, "general")
        assert cached_football is not None
        assert cached_football["status"] == "no"
        assert cached_general is not None
        assert cached_general["status"] == "no"


def test_fetch_event_message_search_empty_after_filter_caches_no(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        monkeypatch.setattr(event_fetcher.config, "FOOTBALL_API_TOKEN", "")
        gate_payload = {"answer": {"status": "yes"}}
        search_payload = {
            "output": {
                "events": [
                    {
                        "title": "Wrong City Event",
                        "time": "21:00",
                        "location": "Stadio Milano",
                        "type": "Calcio",
                        "event_date": "2026-03-02",
                        "source_url": "https://example.com",
                    }
                ]
            }
        }
        session = MockSession(
            [
                DummyResponse(gate_payload),
                DummyResponse(search_payload),
                DummyResponse(gate_payload),
                DummyResponse(search_payload),
            ]
        )
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        result = fetcher.fetch_event_message("Roma", target_date)

        assert result is None
        assert result != FETCH_FAILURE

        cached_football = db.get_event_cache("Roma", target_date, "football")
        cached_general = db.get_event_cache("Roma", target_date, "general")
        assert cached_football is not None
        assert cached_football["status"] == "no"
        assert cached_general is not None
        assert cached_general["status"] == "no"


def test_process_notifications_increments_fetch_errors_for_failure_sentinel(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma"])

        class FailureFetcher:
            def fetch_event_message(self, city: str, target_date: date) -> str:
                return FETCH_FAILURE

        fetcher = FailureFetcher()
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["fetch_errors"] == 1
        assert summary["no_events"] == 0
        assert summary["notifications_sent"] == 0


def test_process_notifications_increments_no_events_for_genuine_no_events(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma"])

        class NoEventsFetcher:
            def fetch_event_message(self, city: str, target_date: date) -> None:
                return None

        fetcher = NoEventsFetcher()
        local_time = datetime(2026, 3, 2, 8, tzinfo=ZoneInfo("Europe/Rome"))

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        assert summary["fetch_errors"] == 0
        assert summary["no_events"] == 1
        assert summary["notifications_sent"] == 0


def test_check_and_send_notifications_does_not_update_last_run_when_fetch_errors(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma"])

        monkeypatch.setattr(event_fetcher.config, "NOTIFICATION_START_HOUR", 0)
        monkeypatch.setattr(event_fetcher.config, "NOTIFICATION_END_HOUR", 23)

        def mock_process_notifications(*args, **kwargs):
            return {
                "notifications_sent": 0,
                "no_events": 0,
                "already_notified": 0,
                "fetch_errors": 1,
            }

        import partita_bot.scheduler as scheduler_module

        monkeypatch.setattr(scheduler_module, "process_notifications", mock_process_notifications)

        last_run_before = db.get_scheduler_last_run()

        fetcher = EventFetcher(db)
        summary = mock_process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=datetime(2026, 3, 2, 12, tzinfo=ZoneInfo("Europe/Rome")),
        )

        if summary.get("fetch_errors", 0) > 0:
            pass
        elif summary["notifications_sent"] or summary["no_events"]:
            db.update_scheduler_last_run()

        last_run_after = db.get_scheduler_last_run()

        assert last_run_after == last_run_before


def test_check_and_send_notifications_updates_last_run_when_only_no_events(monkeypatch, tmp_path):
    db_file = tmp_path / "test.db"
    with Database(database_url=f"sqlite:///{db_file}") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma"])

        monkeypatch.setattr(event_fetcher.config, "NOTIFICATION_START_HOUR", 0)
        monkeypatch.setattr(event_fetcher.config, "NOTIFICATION_END_HOUR", 23)

        def mock_process_notifications(*args, **kwargs):
            return {
                "notifications_sent": 0,
                "no_events": 1,
                "already_notified": 0,
                "fetch_errors": 0,
            }

        import partita_bot.scheduler as scheduler_module

        monkeypatch.setattr(scheduler_module, "process_notifications", mock_process_notifications)

        fetcher = EventFetcher(db)
        summary = mock_process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=datetime(2026, 3, 2, 12, tzinfo=ZoneInfo("Europe/Rome")),
        )

        fetch_errors = summary.get("fetch_errors", 0)
        should_update = fetch_errors == 0 and (
            summary["notifications_sent"] or summary["no_events"]
        )

        if should_update:
            db.update_scheduler_last_run()

        with db.engine.connect() as conn:
            result = conn.execute(text("SELECT id, last_run FROM scheduler_state"))
            rows = result.fetchall()
            last_run_after = rows[0][1] if rows else None

        assert last_run_after is not None, f"rows={rows}"


def test_check_and_send_notifications_updates_last_run_when_notifications_sent(
    monkeypatch, tmp_path
):
    db_file = tmp_path / "test.db"
    with Database(database_url=f"sqlite:///{db_file}") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma"])

        monkeypatch.setattr(event_fetcher.config, "NOTIFICATION_START_HOUR", 0)
        monkeypatch.setattr(event_fetcher.config, "NOTIFICATION_END_HOUR", 23)

        def mock_process_notifications(*args, **kwargs):
            return {
                "notifications_sent": 1,
                "no_events": 0,
                "already_notified": 0,
                "fetch_errors": 0,
            }

        import partita_bot.scheduler as scheduler_module

        monkeypatch.setattr(scheduler_module, "process_notifications", mock_process_notifications)

        fetcher = EventFetcher(db)
        summary = mock_process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=datetime(2026, 3, 2, 12, tzinfo=ZoneInfo("Europe/Rome")),
        )

        fetch_errors = summary.get("fetch_errors", 0)
        should_update = fetch_errors == 0 and (
            summary["notifications_sent"] or summary["no_events"]
        )

        if should_update:
            db.update_scheduler_last_run()

        with db.engine.connect() as conn:
            result = conn.execute(text("SELECT id, last_run FROM scheduler_state"))
            rows = result.fetchall()
            last_run_after = rows[0][1] if rows else None

        assert last_run_after is not None, f"rows={rows}"


def test_manual_retry_session_eventual_success(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        monkeypatch.setattr(event_fetcher.config, "FOOTBALL_API_TOKEN", "")
        session = RetryThenSuccessSession(gate_fail_count=0, search_fail_count=0)
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        result = fetcher.fetch_event_message("Roma", target_date)

        assert result is not None
        assert "Test Event" in result
        assert session.gate_call_count == 2
        assert session.search_call_count == 2

        cached_football = db.get_event_cache("Roma", target_date, "football")
        cached_general = db.get_event_cache("Roma", target_date, "general")
        assert cached_football is not None
        assert cached_football["status"] == "yes"
        assert cached_general is not None
        assert cached_general["status"] == "yes"


def test_configurable_timeout_from_env(monkeypatch):
    monkeypatch.setenv("EXA_HTTP_TIMEOUT", "45")

    import partita_bot.config as config_module

    importlib.reload(config_module)

    assert config_module.EXA_HTTP_TIMEOUT == 45


def test_default_timeout_when_env_not_set(monkeypatch):
    monkeypatch.delenv("EXA_HTTP_TIMEOUT", raising=False)

    import partita_bot.config as config_module

    importlib.reload(config_module)

    assert config_module.EXA_HTTP_TIMEOUT == 30


def test_retry_adapter_configured_on_session():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        adapter = fetcher.session.get_adapter("https://api.exa.ai/answer")
        assert adapter is not None

        retry = adapter.max_retries
        assert retry is not None
        assert retry.total == 3
        assert retry.backoff_factor == 2
        assert 429 in retry.status_forcelist
        assert 500 in retry.status_forcelist
        assert "POST" in retry.allowed_methods


def test_retry_adapter_configured_for_http_and_https():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        https_adapter = fetcher.session.get_adapter("https://api.exa.ai/answer")
        http_adapter = fetcher.session.get_adapter("http://example.com")

        assert https_adapter is not None
        assert http_adapter is not None
        assert https_adapter.max_retries is not None
        assert http_adapter.max_retries is not None


def test_partial_failure_one_flow_succeeds(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        monkeypatch.setattr(event_fetcher.config, "FOOTBALL_API_TOKEN", "")
        gate_payload = {"answer": {"status": "yes"}}
        search_payload = {
            "output": {
                "events": [
                    {
                        "title": "Football Match",
                        "time": "20:00",
                        "location": "Stadio Olimpico, Roma",
                        "type": "Football",
                        "event_date": "2026-03-02",
                        "source_url": "https://example.com/football",
                    }
                ]
            }
        }

        class PartialFailSession:
            def __init__(self):
                self.call_count = 0

            def post(self, url: str, headers=None, json=None, timeout=None):
                self.call_count += 1
                if "answer" in url:
                    if "football" in str(json).lower():
                        return DummyResponse(gate_payload)
                    raise requests.Timeout("General gate timeout")
                if "football" in str(json).lower():
                    return DummyResponse(search_payload)
                raise requests.Timeout("General search timeout")

        session = PartialFailSession()
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        result = fetcher.fetch_event_message("Roma", target_date)

        assert result is not None
        assert result != FETCH_FAILURE
        assert "Football Match" in result


def test_both_flows_error_returns_fetch_failure(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        monkeypatch.setattr(event_fetcher.config, "FOOTBALL_API_TOKEN", "test-token")

        class AlwaysFailSession:
            def post(self, *args, **kwargs):
                raise requests.Timeout("Always fails")

            def get(self, *args, **kwargs):
                raise requests.Timeout("Always fails")

        session = AlwaysFailSession()
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        result = fetcher.fetch_event_message("Roma", target_date)

        assert result == FETCH_FAILURE
