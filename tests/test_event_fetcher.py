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
        assert "orari, location, tipo e dettagli rilevanti" in lower_query
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
        db.save_event_cache("Roma", target_date, "yes", [{"title": "Cached", "time": "19:00"}])

        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")

        fetcher = EventFetcher(db, http_client=cast(requests.Session, FailingSession()))
        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Cached" in message


def test_output_schema_requires_events():
    schema = event_fetcher.OUTPUT_SCHEMA
    assert schema["required"] == ["status", "events"]
    event_items = schema["properties"]["events"]["items"]
    assert event_items["required"] == ["title", "time"]
    assert set(event_items["properties"]) >= {
        "title",
        "time",
        "location",
        "type",
        "details",
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
    assert "orari, location, tipo e dettagli rilevanti" in lower_query
