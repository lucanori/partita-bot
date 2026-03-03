from datetime import date
from typing import cast

import requests

import partita_bot.event_fetcher as event_fetcher
from partita_bot.event_fetcher import EventFetcher
from partita_bot.storage import Database


class DummyResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class MockSession:
    def __init__(self, response: DummyResponse):
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response


class FailingSession:
    def post(self, *args, **kwargs):  # pragma: no cover - ensures cache is used
        raise AssertionError("Exa Answer should not be invoked when cache is fresh")


def test_event_fetcher_formats_and_caches_data(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "status": "yes",
                "events": [
                    {
                        "title": "Finale",
                        "time": "21:00",
                        "location": "Stadio",
                        "type": "Calcio",
                        "details": "Coppa Italia",
                        "event_date": "2026-03-02",
                    }
                ],
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Finale" in message
        assert session.calls
        query = session.calls[0]["json"]["query"]
        lower_query = query.lower()
        assert "rispondi in italiano" in lower_query
        assert "in data 02/03/2026 ci sarà" in lower_query
        assert "nella seguente città: roma?" in lower_query
        assert "status='yes'" in lower_query
        assert "events" in lower_query
        assert "orari, location, tipo, dettagli rilevanti" in lower_query
        assert "event_date" in lower_query
        assert "yyyy-mm-dd" in lower_query
        assert "includi solo eventi" in lower_query
        assert session.calls[0]["headers"]["x-api-key"] == "test-key"

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "yes"
        assert cached["events"]


def test_event_fetcher_handles_no_events(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        session = MockSession(DummyResponse({"answer": {"status": "no", "events": []}}))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        assert fetcher.fetch_event_message("Roma", target_date) is None
        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "no"


def test_event_fetcher_uses_cache_before_calling_api(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        target_date = date(2026, 3, 2)
        db.save_event_cache(
            "Roma",
            target_date,
            "yes",
            [{"title": "Cached", "time": "19:00", "event_date": "2026-03-02"}],
        )

        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")

        fetcher = EventFetcher(db, http_client=cast(requests.Session, FailingSession()))
        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Cached" in message


def test_output_schema_requires_events():
    schema = event_fetcher.OUTPUT_SCHEMA
    assert schema["required"] == ["status", "events"]
    event_items = schema["properties"]["events"]["items"]
    assert event_items["required"] == ["title", "time", "event_date"]
    assert set(event_items["properties"]) >= {
        "title",
        "time",
        "location",
        "type",
        "details",
        "event_date",
    }


def test_build_query_includes_guidance():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)
        query = fetcher._build_query("Parma", date(2026, 2, 27))

    lower_query = query.lower()
    assert lower_query.startswith("rispondi in italiano")
    assert "in data 27/02/2026 ci sarà" in lower_query
    assert "nella seguente città: parma?" in lower_query
    assert "status='yes'" in query
    assert "events=[]" in query
    assert "orari, location, tipo, dettagli rilevanti" in lower_query
    assert "event_date" in lower_query
    assert "yyyy-mm-dd" in lower_query
    assert "includi solo eventi" in lower_query
    assert "2026-02-27" in query


def test_extract_payload_filters_events_with_wrong_date(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "status": "yes",
                "events": [
                    {
                        "title": "Correct Event",
                        "time": "21:00",
                        "event_date": "2026-03-02",
                    },
                    {
                        "title": "Wrong Date Event",
                        "time": "20:00",
                        "event_date": "2026-03-03",
                    },
                ],
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Correct Event" in message
        assert "Wrong Date Event" not in message

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "yes"
        assert len(cached["events"]) == 1
        assert cached["events"][0]["title"] == "Correct Event"


def test_extract_payload_filters_events_missing_event_date(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "status": "yes",
                "events": [
                    {
                        "title": "Valid Event",
                        "time": "21:00",
                        "event_date": "2026-03-02",
                    },
                    {
                        "title": "Missing Date Event",
                        "time": "20:00",
                    },
                ],
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Valid Event" in message
        assert "Missing Date Event" not in message

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "yes"
        assert len(cached["events"]) == 1


def test_extract_payload_treats_as_no_events_when_all_filtered(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "status": "yes",
                "events": [
                    {
                        "title": "Wrong Date Event 1",
                        "time": "21:00",
                        "event_date": "2026-03-03",
                    },
                    {
                        "title": "Missing Date Event",
                        "time": "20:00",
                    },
                ],
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is None

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "no"
        assert cached["events"] == []


def test_cached_events_missing_event_date_are_filtered_and_cache_updated():
    with Database(database_url="sqlite:///:memory:") as db:
        target_date = date(2026, 3, 2)
        legacy_events = [
            {"title": "Legacy Event 1", "time": "19:00"},
            {"title": "Legacy Event 2", "time": "20:00"},
        ]
        db.save_event_cache("Roma", target_date, "yes", legacy_events)

        fetcher = EventFetcher(db, http_client=cast(requests.Session, FailingSession()))
        message = fetcher.fetch_event_message("Roma", target_date)

        assert message is None

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "no"
        assert cached["events"] == []


def test_cached_events_with_wrong_date_are_filtered_and_cache_updated():
    with Database(database_url="sqlite:///:memory:") as db:
        target_date = date(2026, 3, 2)
        wrong_date_events = [
            {"title": "Yesterday Event", "time": "19:00", "event_date": "2026-03-01"},
            {"title": "Tomorrow Event", "time": "20:00", "event_date": "2026-03-03"},
        ]
        db.save_event_cache("Roma", target_date, "yes", wrong_date_events)

        fetcher = EventFetcher(db, http_client=cast(requests.Session, FailingSession()))
        message = fetcher.fetch_event_message("Roma", target_date)

        assert message is None

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "no"
        assert cached["events"] == []


def test_cached_mixed_events_only_valid_ones_sent_and_re_cached():
    with Database(database_url="sqlite:///:memory:") as db:
        target_date = date(2026, 3, 2)
        mixed_events = [
            {"title": "Valid Today Event", "time": "19:00", "event_date": "2026-03-02"},
            {"title": "Legacy Missing Date", "time": "20:00"},
            {"title": "Wrong Date Event", "time": "21:00", "event_date": "2026-03-01"},
            {"title": "Another Valid Event", "time": "22:00", "event_date": "2026-03-02"},
        ]
        db.save_event_cache("Roma", target_date, "yes", mixed_events)

        fetcher = EventFetcher(db, http_client=cast(requests.Session, FailingSession()))
        message = fetcher.fetch_event_message("Roma", target_date)

        assert message is not None
        assert "Valid Today Event" in message
        assert "Another Valid Event" in message
        assert "Legacy Missing Date" not in message
        assert "Wrong Date Event" not in message

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "yes"
        assert len(cached["events"]) == 2
        titles = [e["title"] for e in cached["events"]]
        assert "Valid Today Event" in titles
        assert "Another Valid Event" in titles
        assert "Legacy Missing Date" not in titles
        assert "Wrong Date Event" not in titles


def test_extract_payload_keeps_only_exact_date_events(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "status": "yes",
                "events": [
                    {
                        "title": "Today Event 1",
                        "time": "15:00",
                        "event_date": "2026-03-02",
                    },
                    {
                        "title": "Yesterday Event",
                        "time": "20:00",
                        "event_date": "2026-03-01",
                    },
                    {
                        "title": "Today Event 2",
                        "time": "21:00",
                        "event_date": "2026-03-02",
                    },
                    {
                        "title": "Tomorrow Event",
                        "time": "18:00",
                        "event_date": "2026-03-03",
                    },
                ],
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Today Event 1" in message
        assert "Today Event 2" in message
        assert "Yesterday Event" not in message
        assert "Tomorrow Event" not in message

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "yes"
        assert len(cached["events"]) == 2
        titles = [e["title"] for e in cached["events"]]
        assert "Today Event 1" in titles
        assert "Today Event 2" in titles
