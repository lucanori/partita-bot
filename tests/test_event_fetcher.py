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
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.call_index < len(self.responses):
            response = self.responses[self.call_index]
            self.call_index += 1
            return response
        return DummyResponse({})


class FailingSession:
    def post(self, *args, **kwargs):
        raise AssertionError("Exa should not be invoked when cache is fresh")


def test_event_fetcher_gate_no_no_search_call(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        gate_payload = {"answer": {"status": "no"}}
        session = MockSession(DummyResponse(gate_payload))
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is None
        assert len(session.calls) == 1
        assert session.calls[0]["url"] == event_fetcher.EXA_ANSWER_ENDPOINT

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "no"


def test_event_fetcher_gate_yes_search_called_with_links(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        gate_payload = {"answer": {"status": "yes"}}
        search_payload = {
            "output": {
                "events": [
                    {
                        "title": "Finale",
                        "time": "21:00",
                        "location": "Stadio Olimpico, Roma",
                        "type": "Calcio",
                        "details": "Coppa Italia",
                        "event_date": "2026-03-02",
                        "source_url": "https://example.com/event1",
                    }
                ]
            }
        }
        session = MockSession([DummyResponse(gate_payload), DummyResponse(search_payload)])
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Finale" in message
        assert "🔗 https://example.com/event1" in message
        assert len(session.calls) == 2
        assert session.calls[0]["url"] == event_fetcher.EXA_ANSWER_ENDPOINT
        assert session.calls[1]["url"] == event_fetcher.EXA_SEARCH_ENDPOINT

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "yes"
        assert len(cached["events"]) == 1
        assert cached["events"][0]["source_url"] == "https://example.com/event1"


def test_search_filters_wrong_date(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        gate_payload = {"answer": {"status": "yes"}}
        search_payload = {
            "output": {
                "events": [
                    {
                        "title": "Correct Event",
                        "time": "21:00",
                        "location": "Stadio Olimpico, Roma",
                        "type": "Calcio",
                        "event_date": "2026-03-02",
                        "source_url": "https://example.com/correct",
                    },
                    {
                        "title": "Wrong Date Event",
                        "time": "20:00",
                        "location": "Stadio Olimpico, Roma",
                        "type": "Calcio",
                        "event_date": "2026-03-03",
                        "source_url": "https://example.com/wrong",
                    },
                ]
            }
        }
        session = MockSession([DummyResponse(gate_payload), DummyResponse(search_payload)])
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Correct Event" in message
        assert "Wrong Date Event" not in message

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert len(cached["events"]) == 1
        assert cached["events"][0]["title"] == "Correct Event"


def test_search_filters_wrong_city(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        gate_payload = {"answer": {"status": "yes"}}
        search_payload = {
            "output": {
                "events": [
                    {
                        "title": "Roma Event",
                        "time": "21:00",
                        "location": "Stadio Olimpico, Roma",
                        "type": "Calcio",
                        "event_date": "2026-03-02",
                        "source_url": "https://example.com/roma",
                    },
                    {
                        "title": "Cremona Event",
                        "time": "20:00",
                        "location": "Stadio Cremona",
                        "type": "Calcio",
                        "event_date": "2026-03-02",
                        "source_url": "https://example.com/cremona",
                    },
                ]
            }
        }
        session = MockSession([DummyResponse(gate_payload), DummyResponse(search_payload)])
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Roma Event" in message
        assert "Cremona Event" not in message

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert len(cached["events"]) == 1
        assert cached["events"][0]["title"] == "Roma Event"


def test_search_filters_missing_source_url(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        gate_payload = {"answer": {"status": "yes"}}
        search_payload = {
            "output": {
                "events": [
                    {
                        "title": "Valid Event",
                        "time": "21:00",
                        "location": "Stadio Olimpico, Roma",
                        "type": "Calcio",
                        "event_date": "2026-03-02",
                        "source_url": "https://example.com/valid",
                    },
                    {
                        "title": "Missing URL Event",
                        "time": "20:00",
                        "location": "Stadio Olimpico, Roma",
                        "type": "Calcio",
                        "event_date": "2026-03-02",
                    },
                ]
            }
        }
        session = MockSession([DummyResponse(gate_payload), DummyResponse(search_payload)])
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Valid Event" in message
        assert "Missing URL Event" not in message

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert len(cached["events"]) == 1


def test_cache_revalidation_filters_legacy_events(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        target_date = date(2026, 3, 2)
        legacy_events = [
            {
                "title": "Legacy No URL",
                "time": "19:00",
                "event_date": "2026-03-02",
                "location": "Roma",
            },
            {
                "title": "Legacy Wrong City",
                "time": "20:00",
                "event_date": "2026-03-02",
                "source_url": "https://example.com",
                "location": "Milano",
            },
            {
                "title": "Valid Legacy",
                "time": "21:00",
                "event_date": "2026-03-02",
                "source_url": "https://example.com",
                "location": "Roma",
            },
        ]
        db.save_event_cache("Roma", target_date, "yes", legacy_events)

        fetcher = EventFetcher(db, http_client=cast(requests.Session, FailingSession()))
        message = fetcher.fetch_event_message("Roma", target_date)

        assert message is not None
        assert "Valid Legacy" in message
        assert "Legacy No URL" not in message
        assert "Legacy Wrong City" not in message

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert len(cached["events"]) == 1
        assert cached["events"][0]["title"] == "Valid Legacy"


def test_cache_revalidation_returns_none_when_all_legacy_invalid(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        target_date = date(2026, 3, 2)
        legacy_events = [
            {
                "title": "Legacy No URL",
                "time": "19:00",
                "event_date": "2026-03-02",
                "location": "Roma",
            },
            {
                "title": "Legacy Wrong City",
                "time": "20:00",
                "event_date": "2026-03-02",
                "source_url": "https://example.com",
                "location": "Milano",
            },
        ]
        db.save_event_cache("Roma", target_date, "yes", legacy_events)

        fetcher = EventFetcher(db, http_client=cast(requests.Session, FailingSession()))
        message = fetcher.fetch_event_message("Roma", target_date)

        assert message is None

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "no"


def test_city_classification_prompt_prefers_city_country_format(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        session = MockSession(
            DummyResponse({"answer": {"is_city": True, "canonical_name": "Parma, Italy"}})
        )
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))

        query = fetcher._build_classification_query("Parma")
        assert "City, Country" in query
        assert "Parma, Italy" in query or "city with country" in query.lower()

        is_city, canonical = fetcher.classify_city("Parma")
        assert is_city is True
        assert canonical == "parma, italy"


def test_event_fetcher_uses_cache_before_calling_api(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        target_date = date(2026, 3, 2)
        db.save_event_cache(
            "Roma",
            target_date,
            "yes",
            [
                {
                    "title": "Cached",
                    "time": "19:00",
                    "event_date": "2026-03-02",
                    "source_url": "https://example.com/cached",
                    "location": "Roma",
                }
            ],
        )

        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")

        fetcher = EventFetcher(db, http_client=cast(requests.Session, FailingSession()))
        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is not None
        assert "Cached" in message


def test_gate_output_schema_requires_status_only():
    schema = event_fetcher.GATE_OUTPUT_SCHEMA
    assert schema["required"] == ["status"]
    assert "status" in schema["properties"]


def test_search_output_schema_requires_source_url():
    schema = event_fetcher.SEARCH_OUTPUT_SCHEMA
    event_items = schema["properties"]["events"]["items"]
    assert "source_url" in event_items["required"]
    assert "source_url" in event_items["properties"]


def test_build_gate_query_includes_guidance():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)
        query = fetcher._build_gate_query("Parma", date(2026, 2, 27))

    lower_query = query.lower()
    assert "status='yes'" in lower_query or "status='no'" in lower_query
    assert "only" in lower_query
    assert "parma" in lower_query


def test_build_search_query_includes_source_url_requirement_and_language(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "BOT_LANGUAGE", "Italian")
        fetcher = EventFetcher(db)
        query = fetcher._build_search_query("Parma", date(2026, 2, 27))

    lower_query = query.lower()
    assert "source_url" in lower_query
    assert "respond in italian" in lower_query


def test_build_search_query_uses_default_english_language(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "BOT_LANGUAGE", "English")
        fetcher = EventFetcher(db)
        query = fetcher._build_search_query("Parma", date(2026, 2, 27))

    lower_query = query.lower()
    assert "respond in english" in lower_query
    assert "find sports events" in lower_query


def test_no_valid_events_after_filtering_caches_no(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
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
                    },
                ]
            }
        }
        session = MockSession([DummyResponse(gate_payload), DummyResponse(search_payload)])
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        message = fetcher.fetch_event_message("Roma", target_date)
        assert message is None

        cached = db.get_event_cache("Roma", target_date)
        assert cached is not None
        assert cached["status"] == "no"


def test_event_matches_city_checks_location_title_details():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        event_in_location = {
            "title": "Concert",
            "location": "Stadio Olimpico, Roma",
            "details": "Great show",
            "event_date": "2026-03-02",
            "source_url": "https://example.com",
        }
        assert fetcher._event_matches_city(event_in_location, "Roma") is True

        event_in_title = {
            "title": "Roma vs Milan",
            "location": "Stadio Nazionale",
            "details": "Match",
            "event_date": "2026-03-02",
            "source_url": "https://example.com",
        }
        assert fetcher._event_matches_city(event_in_title, "Roma") is True

        event_in_details = {
            "title": "Concert",
            "location": "Stadio Nazionale",
            "details": "Event in Roma city center",
            "event_date": "2026-03-02",
            "source_url": "https://example.com",
        }
        assert fetcher._event_matches_city(event_in_details, "Roma") is True

        event_no_match = {
            "title": "Milano Event",
            "location": "Stadio San Siro, Milano",
            "details": "Match in Milano",
            "event_date": "2026-03-02",
            "source_url": "https://example.com",
        }
        assert fetcher._event_matches_city(event_no_match, "Roma") is False


def test_event_matches_city_does_not_match_country_only():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        event_italy_only = {
            "title": "National Event",
            "location": "Various locations across Italy",
            "details": "A nationwide celebration",
            "event_date": "2026-03-02",
            "source_url": "https://example.com",
        }
        assert fetcher._event_matches_city(event_italy_only, "Parma, Italy") is False

        event_parma_only = {
            "title": "Parma Event",
            "location": "Stadium in Parma",
            "details": "Local match",
            "event_date": "2026-03-02",
            "source_url": "https://example.com",
        }
        assert fetcher._event_matches_city(event_parma_only, "Parma, Italy") is True


def test_event_matches_city_multi_token_city_core():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        event_new_york = {
            "title": "NYC Concert",
            "location": "Madison Square Garden, New York",
            "details": "Live performance",
            "event_date": "2026-03-02",
            "source_url": "https://example.com",
        }
        assert fetcher._event_matches_city(event_new_york, "New York, United States") is True

        event_york_only = {
            "title": "York Event",
            "location": "Historic York, UK",
            "details": "Medieval festival",
            "event_date": "2026-03-02",
            "source_url": "https://example.com",
        }
        assert fetcher._event_matches_city(event_york_only, "New York, United States") is False


def test_search_payload_uses_deep_type(monkeypatch):
    with Database(database_url="sqlite:///:memory:") as db:
        monkeypatch.setattr(event_fetcher.config, "EXA_API_KEY", "test-key")
        gate_payload = {"answer": {"status": "yes"}}
        search_payload = {"output": {"events": []}}
        session = MockSession([DummyResponse(gate_payload), DummyResponse(search_payload)])
        fetcher = EventFetcher(db, http_client=cast(requests.Session, session))
        target_date = date(2026, 3, 2)

        fetcher.fetch_event_message("Roma", target_date)

        search_call = session.calls[1]
        assert search_call["json"]["type"] == "deep"


def test_extract_search_payload_handles_output_content_events():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        payload_output_content = {
            "output": {
                "content": {
                    "events": [
                        {
                            "title": "Test Event",
                            "time": "20:00",
                            "location": "Stadium",
                            "type": "Sport",
                            "event_date": "2026-03-02",
                            "source_url": "https://example.com",
                        }
                    ]
                }
            }
        }
        result = fetcher._extract_search_payload(payload_output_content)
        assert result is not None
        assert len(result["events"]) == 1
        assert result["events"][0]["title"] == "Test Event"


def test_extract_search_payload_handles_direct_events():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        payload_direct = {
            "events": [
                {
                    "title": "Direct Event",
                    "time": "21:00",
                    "location": "Arena",
                    "type": "Concert",
                    "event_date": "2026-03-02",
                    "source_url": "https://example.com/direct",
                }
            ]
        }
        result = fetcher._extract_search_payload(payload_direct)
        assert result is not None
        assert len(result["events"]) == 1
        assert result["events"][0]["title"] == "Direct Event"


def test_extract_search_payload_handles_output_events():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)

        payload_output = {
            "output": {
                "events": [
                    {
                        "title": "Output Event",
                        "time": "19:00",
                        "location": "Hall",
                        "type": "Theater",
                        "event_date": "2026-03-02",
                        "source_url": "https://example.com/output",
                    }
                ]
            }
        }
        result = fetcher._extract_search_payload(payload_output)
        assert result is not None
        assert len(result["events"]) == 1
        assert result["events"][0]["title"] == "Output Event"
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)
        events = [
            {
                "title": "Test Event",
                "time": "20:00",
                "location": "Stadio",
                "type": "Calcio",
                "details": "Test details",
                "source_url": "https://example.com/event",
            }
        ]
        message = fetcher._format_event_message("Roma", date(2026, 3, 2), events)
        assert "🔗 https://example.com/event" in message


def test_format_event_message_omits_link_when_missing():
    with Database(database_url="sqlite:///:memory:") as db:
        fetcher = EventFetcher(db)
        events = [
            {
                "title": "Test Event",
                "time": "20:00",
                "location": "Stadio",
                "type": "Calcio",
            }
        ]
        message = fetcher._format_event_message("Roma", date(2026, 3, 2), events)
        assert "🔗" not in message
