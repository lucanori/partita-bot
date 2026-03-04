from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import requests

import partita_bot.config as config
from partita_bot.storage import Database

LOGGER = logging.getLogger(__name__)
EXA_ANSWER_ENDPOINT = "https://api.exa.ai/answer"
EXA_SEARCH_ENDPOINT = "https://api.exa.ai/search"
HTTP_TIMEOUT = 15

GATE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["yes", "no"]},
    },
    "required": ["status"],
}

SEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "time": {"type": "string"},
                    "location": {"type": "string"},
                    "type": {"type": "string"},
                    "details": {"type": "string"},
                    "event_date": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["title", "time", "event_date", "source_url"],
            },
        },
    },
    "required": ["events"],
}

CITY_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_city": {"type": "boolean"},
        "canonical_name": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["is_city"],
}


class EventFetcher:
    def __init__(self, db: Database, http_client: requests.Session | None = None):
        self.db = db
        self.session = http_client or requests.Session()

    def _normalize_for_matching(self, text: str) -> set[str]:
        if not text:
            return set()
        normalized = text.lower()
        for char in ",.-_()[]{}:;/\\|":
            normalized = normalized.replace(char, " ")
        tokens = normalized.split()
        return set(tokens)

    def _extract_city_core(self, city: str) -> str:
        normalized = city.lower().strip()
        if "," in normalized:
            normalized = normalized.split(",")[0].strip()
        return normalized

    def _event_matches_city(self, event: dict[str, Any], target_city: str) -> bool:
        city_core = self._extract_city_core(target_city)
        if not city_core:
            return False
        city_core_tokens = set(city_core.split())
        searchable_fields = [
            event.get("location", ""),
            event.get("title", ""),
            event.get("details", ""),
        ]
        for field in searchable_fields:
            if not field:
                continue
            field_normalized = field.lower()
            for char in ",.-_()[]{}:;/\\|":
                field_normalized = field_normalized.replace(char, " ")
            if city_core in field_normalized:
                return True
            field_tokens = set(field_normalized.split())
            if city_core_tokens <= field_tokens:
                return True
        return False

    def _filter_events(
        self,
        events: list[dict[str, Any]],
        target_date: date,
        target_city: str,
    ) -> list[dict[str, Any]]:
        target_iso = target_date.isoformat()
        valid_events: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_date = event.get("event_date")
            if not event_date:
                LOGGER.debug("Filtering out event missing event_date: %s", event.get("title"))
                continue
            if event_date != target_iso:
                LOGGER.debug(
                    "Filtering out event with wrong date: %s (expected %s, got %s)",
                    event.get("title"),
                    target_iso,
                    event_date,
                )
                continue
            source_url = event.get("source_url")
            if not source_url:
                LOGGER.debug("Filtering out event missing source_url: %s", event.get("title"))
                continue
            if not self._event_matches_city(event, target_city):
                LOGGER.debug(
                    "Filtering out event not matching city '%s': %s",
                    target_city,
                    event.get("title"),
                )
                continue
            valid_events.append(event)
        return valid_events

    def fetch_event_message(self, city: str, target_date: date | None = None) -> str | None:
        normalized_city = self.db.normalize_city(city)
        if not normalized_city:
            LOGGER.debug("Skipping event lookup because city is empty")
            return None

        target_date = target_date or datetime.now(config.TIMEZONE_INFO).date()

        cached = self.db.get_event_cache(city, target_date)
        if cached:
            status = str(cached.get("status", "")).lower()
            events = cached.get("events") or []
            if status == "yes" and events:
                valid_events = self._filter_events(events, target_date, city)
                if not valid_events:
                    LOGGER.info(
                        "Cached events had no valid entries for date %s, updating cache",
                        target_date,
                    )
                    self.db.save_event_cache(city, target_date, "no", [])
                    return None
                if len(valid_events) != len(events):
                    LOGGER.info(
                        "Filtered cached events from %d to %d valid entries",
                        len(events),
                        len(valid_events),
                    )
                    self.db.save_event_cache(city, target_date, "yes", valid_events)
                return self._format_event_message(city, target_date, valid_events)
            return None

        gate_result = self._call_exa_gate(city, target_date)
        if not gate_result:
            return None

        gate_status = str(gate_result.get("status", "")).lower()

        if gate_status != "yes":
            self.db.save_event_cache(city, target_date, "no", [])
            return None

        search_result = self._call_exa_search(city, target_date)
        if not search_result:
            self.db.save_event_cache(city, target_date, "no", [])
            return None

        events = search_result.get("events") or []
        valid_events = self._filter_events(events, target_date, city)

        if not valid_events:
            LOGGER.info("No valid events remain after filtering for %s on %s", city, target_date)
            self.db.save_event_cache(city, target_date, "no", [])
            return None

        self.db.save_event_cache(city, target_date, "yes", valid_events)
        return self._format_event_message(city, target_date, valid_events)

    def _call_exa_gate(self, city: str, target_date: date) -> dict[str, Any] | None:
        if not config.EXA_API_KEY:
            LOGGER.error("Cannot query Exa Answer because EXA_API_KEY is missing")
            return None

        payload = {
            "query": self._build_gate_query(city, target_date),
            "outputSchema": GATE_OUTPUT_SCHEMA,
        }
        headers = {
            "x-api-key": config.EXA_API_KEY,
            "Content-Type": "application/json",
        }

        try:
            response = self.session.post(
                EXA_ANSWER_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            self._record_cost_from_response(data, "answer")
            return self._extract_gate_payload(data)
        except requests.RequestException as exc:
            LOGGER.error("Exa Answer gate request failed: %s", exc)
            return None
        except ValueError as exc:
            LOGGER.error("Exa Answer gate response could not be decoded: %s", exc)
            return None

    def _call_exa_search(self, city: str, target_date: date) -> dict[str, Any] | None:
        if not config.EXA_API_KEY:
            LOGGER.error("Cannot query Exa Search because EXA_API_KEY is missing")
            return None

        payload = {
            "query": self._build_search_query(city, target_date),
            "useAutoprompt": True,
            "type": "deep",
            "outputSchema": SEARCH_OUTPUT_SCHEMA,
        }
        headers = {
            "x-api-key": config.EXA_API_KEY,
            "Content-Type": "application/json",
        }

        try:
            response = self.session.post(
                EXA_SEARCH_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            self._record_cost_from_response(data, "search")
            return self._extract_search_payload(data)
        except requests.RequestException as exc:
            LOGGER.error("Exa Search request failed: %s", exc)
            return None
        except ValueError as exc:
            LOGGER.error("Exa Search response could not be decoded: %s", exc)
            return None

    def _build_gate_query(self, city: str, target_date: date) -> str:
        formatted_date = target_date.strftime("%d/%m/%Y")
        city_name = city.strip() or "the city"
        return (
            f"On {formatted_date}, will there be a football match and/or other relevant events "
            f"such as concerts, shows, etc. in the following city: {city_name}? "
            "Reply ONLY with status='yes' if there are confirmed events, otherwise status='no'. "
            "Do not include event details, only yes/no."
        )

    def _build_search_query(self, city: str, target_date: date) -> str:
        formatted_date = target_date.strftime("%d/%m/%Y")
        iso_date = target_date.isoformat()
        city_name = city.strip() or "the city"
        language = config.BOT_LANGUAGE
        return (
            f"Respond in {language}. "
            f"Find sports events, concerts, shows and other relevant events "
            f"in {city_name} on {formatted_date} ({iso_date}). "
            "For each event, include: title, time, location, type, details, "
            f"event_date (format YYYY-MM-DD: {iso_date}), and source_url (source URL). "
            "Include ONLY events with confirmed date and valid source URL. "
            "Return events in the 'events' field of the output schema."
        )

    def _extract_gate_payload(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            LOGGER.warning("Unexpected payload type from Exa Answer gate: %s", type(raw))
            return None

        candidate = raw
        for key in ("answer", "output", "response", "data"):
            if isinstance(candidate.get(key), dict):
                candidate = candidate[key]
                break

        status = str(candidate.get("status", "")).lower()
        return {"status": status}

    def _extract_search_payload(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            LOGGER.warning("Unexpected payload type from Exa Search: %s", type(raw))
            return None

        candidate = raw
        for key in ("answer", "output", "response", "data"):
            if isinstance(candidate.get(key), dict):
                candidate = candidate[key]
                break

        if isinstance(candidate.get("content"), dict):
            content = candidate["content"]
            if "events" in content:
                events = content["events"]
                if isinstance(events, list):
                    return {"events": events}

        events = candidate.get("events", [])

        if not isinstance(events, list):
            events = []

        return {"events": events}

    def _record_cost_from_response(self, raw: Any, source: str) -> None:
        if not isinstance(raw, dict):
            return
        cost_dollars = raw.get("costDollars")
        if isinstance(cost_dollars, dict):
            total = cost_dollars.get("total")
            if isinstance(total, (int, float)):
                self.db.record_exa_cost(source, float(total))
            else:
                self.db.record_exa_cost(source, 0.0)
        else:
            self.db.record_exa_cost(source, 0.0)

    def _format_event_message(
        self,
        city: str,
        target_date: date,
        events: list[dict[str, Any]],
    ) -> str:
        header = (
            f"📣 {target_date.strftime('%d/%m/%Y')} a {city.title()} ci sono "
            f"{len(events)} eventi rilevanti:\n\n"
        )
        lines: list[str] = [header]
        for entry in events:
            title = entry.get("title", "Evento")
            time = entry.get("time", "Orario non disponibile")
            location = entry.get("location", "Luogo non disponibile")
            event_type = entry.get("type", "Evento")
            details = entry.get("details")
            source_url = entry.get("source_url", "")

            lines.append(f"🕒 {time} – {title} ({event_type})")
            lines.append(f"📍 {location}")
            if details:
                lines.append(f"ℹ️ {details}")
            if source_url:
                lines.append(f"🔗 {source_url}")
            lines.append("")

        return "\n".join(lines).strip()

    def classify_city(self, location: str) -> tuple[bool | None, str]:
        normalized = self.db.normalize_city(location)
        if not normalized:
            return (None, "")
        cached_is_city, cached_canonical = self.db.get_city_classification(normalized)
        if cached_is_city is not None:
            return (cached_is_city, cached_canonical)
        if not config.EXA_API_KEY:
            LOGGER.error("Cannot query Exa for city classification because EXA_API_KEY is missing")
            return (None, "")
        payload = {
            "query": self._build_classification_query(location),
            "outputSchema": CITY_CLASSIFICATION_SCHEMA,
        }
        headers = {
            "x-api-key": config.EXA_API_KEY,
            "Content-Type": "application/json",
        }
        try:
            response = self.session.post(
                EXA_ANSWER_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            self._record_cost_from_response(data, "answer")
            result = self._extract_classification_payload(data)
            if result is not None:
                is_city = result.get("is_city", False)
                canonical_name = result.get("canonical_name", "")
                if is_city and canonical_name:
                    canonical_normalized = self.db.normalize_city(canonical_name)
                else:
                    canonical_normalized = normalized if is_city else ""
                self.db.set_city_classification(normalized, is_city, canonical_normalized)
                return (is_city, canonical_normalized)
            return (None, "")
        except requests.RequestException as exc:
            LOGGER.error("Exa city classification request failed: %s", exc)
            return (None, "")
        except ValueError as exc:
            LOGGER.error("Exa city classification response could not be decoded: %s", exc)
            return (None, "")

    def _build_classification_query(self, location: str) -> str:
        return (
            f'Is "{location}" a city (not a region, province, state, or country)? '
            "Respond with is_city=true if it is a city, is_city=false otherwise. "
            "If it is a city, also provide the canonical city name in canonical_name "
            "preferring the format 'City, Country' when the country is known "
            "(e.g., 'Parma, Italy' instead of just 'Parma'). "
            "Correct any typos in the input (e.g., 'parm a' -> 'Parma, Italy'). "
            "Be strict: only accept well-known cities."
        )

    def _extract_classification_payload(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            LOGGER.warning("Unexpected payload type from Exa city classification: %s", type(raw))
            return None
        candidate = raw
        for key in ("answer", "output", "response", "data"):
            if isinstance(candidate.get(key), dict):
                candidate = candidate[key]
                break
        return {
            "is_city": candidate.get("is_city", False),
            "canonical_name": candidate.get("canonical_name", ""),
            "reason": candidate.get("reason", ""),
        }
