from datetime import datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import requests

import partita_bot.event_fetcher as event_fetcher
from partita_bot.event_fetcher import EventFetcher
from partita_bot.storage import CityClassificationCache, Database


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
    def post(self, *args, **kwargs):
        raise AssertionError("Exa Answer should not be invoked when cache is fresh")


def test_city_classification_schema_includes_canonical_name():
    schema = event_fetcher.CITY_CLASSIFICATION_SCHEMA
    assert "canonical_name" in schema["properties"]
    assert schema["properties"]["canonical_name"]["type"] == "string"


def test_build_classification_query_asks_for_canonical_name():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)
        query = fetcher._build_classification_query("parm a")

    assert "canonical_name" in query.lower()
    assert "correct any typos" in query.lower()
    assert "parm a" in query.lower()


def test_classify_city_returns_canonical_name(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "is_city": True,
                "canonical_name": "Parma",
                "reason": "Major Italian city",
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))

        is_city, canonical = fetcher.classify_city("parm a")

        assert is_city is True
        assert canonical == "parma"
        assert session.calls

        cached_is_city, cached_canonical = db.get_city_classification("parm a")
        assert cached_is_city is True
        assert cached_canonical == "parma"


def test_classify_city_uses_fallback_when_no_canonical(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "is_city": True,
                "reason": "Major city",
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))

        is_city, canonical = fetcher.classify_city("Roma")

        assert is_city is True
        assert canonical == "roma"


def test_classify_city_returns_empty_canonical_for_non_city(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "is_city": False,
                "canonical_name": "",
                "reason": "Not a city",
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))

        is_city, canonical = fetcher.classify_city("Lombardia")

        assert is_city is False
        assert canonical == ""


def test_classification_cache_ttl_expiration(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        old_time = datetime.now(tz=ZoneInfo("UTC")) - timedelta(days=366)
        cache_entry = CityClassificationCache(
            normalized_name="roma",
            is_city=True,
            canonical_name="roma",
            created_at=old_time,
        )
        db.session.add(cache_entry)
        db.session.commit()

        cached_is_city, cached_canonical = db.get_city_classification("roma")
        assert cached_is_city is None
        assert cached_canonical == ""


def test_classification_cache_fresh_entry_not_expired():
    with Database(database_url="sqlite:///:memory:") as db:
        fresh_time = datetime.now(tz=ZoneInfo("UTC")) - timedelta(days=30)
        cache_entry = CityClassificationCache(
            normalized_name="milano",
            is_city=True,
            canonical_name="milano",
            created_at=fresh_time,
        )
        db.session.add(cache_entry)
        db.session.commit()

        cached_is_city, cached_canonical = db.get_city_classification("milano")
        assert cached_is_city is True
        assert cached_canonical == "milano"


def test_clear_city_classification_cache():
    with Database(database_url="sqlite:///:memory:") as db:
        db.set_city_classification("roma", True, "roma")
        db.set_city_classification("milano", True, "milano")
        db.set_city_classification("napoli", False, "")

        count = db.clear_city_classification_cache()
        assert count == 3

        cached_is_city, _ = db.get_city_classification("roma")
        assert cached_is_city is None


def test_typo_correction_yields_canonical_city(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        payload = {
            "answer": {
                "is_city": True,
                "canonical_name": "Parma",
                "reason": "Corrected typo",
            }
        }
        session = MockSession(DummyResponse(payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))

        is_city, canonical = fetcher.classify_city("parm a")

        assert is_city is True
        assert canonical == "parma"

        cached_is_city, cached_canonical = db.get_city_classification("parm a")
        assert cached_is_city is True
        assert cached_canonical == "parma"


def test_extract_classification_payload_handles_canonical_name():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        raw = {
            "answer": {
                "is_city": True,
                "canonical_name": "Firenze",
                "reason": "City",
            }
        }
        result = fetcher._extract_classification_payload(raw)

        assert result["is_city"] is True
        assert result["canonical_name"] == "Firenze"
        assert result["reason"] == "City"


def test_extract_classification_payload_handles_missing_canonical():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        raw = {
            "answer": {
                "is_city": True,
            }
        }
        result = fetcher._extract_classification_payload(raw)

        assert result["is_city"] is True
        assert result["canonical_name"] == ""
        assert result["reason"] == ""
