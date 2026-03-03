from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import requests

import partita_bot.config as config
from partita_bot.storage import Database

LOGGER = logging.getLogger(__name__)
EXA_ENDPOINT = "https://api.exa.ai/answer"
HTTP_TIMEOUT = 15

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["yes", "no"]},
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
                },
                "required": ["title", "time", "event_date"],
            },
        },
    },
    "required": ["status", "events"],
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

    def _filter_events_by_date(
        self,
        events: list[dict[str, Any]],
        target_date: date,
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
                valid_events = self._filter_events_by_date(events, target_date)
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

        payload = self._call_exa(city, target_date)
        if not payload:
            return None

        status = str(payload.get("status", "")).lower()
        events = payload.get("events") or []

        if status != "yes" or not events:
            self.db.save_event_cache(city, target_date, "no", [])
            return None

        self.db.save_event_cache(city, target_date, status, events)
        return self._format_event_message(city, target_date, events)

    def _call_exa(self, city: str, target_date: date) -> dict[str, Any] | None:
        if not config.EXA_API_KEY:
            LOGGER.error("Cannot query Exa Answer because EXA_API_KEY is missing")
            return None

        payload = {
            "query": self._build_query(city, target_date),
            "outputSchema": OUTPUT_SCHEMA,
        }
        headers = {
            "x-api-key": config.EXA_API_KEY,
            "Content-Type": "application/json",
        }

        try:
            response = self.session.post(
                EXA_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            return self._extract_payload(data, target_date)
        except requests.RequestException as exc:
            LOGGER.error("Exa Answer request failed: %s", exc)
            return None
        except ValueError as exc:
            LOGGER.error("Exa Answer response could not be decoded: %s", exc)
            return None

    def _build_query(self, city: str, target_date: date) -> str:
        formatted_date = target_date.strftime("%d/%m/%Y")
        iso_date = target_date.isoformat()
        city_name = city.strip() or "la città"
        return (
            "Rispondi in italiano. "
            f"In data {formatted_date} ci sarà una partita di calcio e/o altri eventi rilevanti "
            f"tipo concerti, spettacoli, ecc. nella seguente città: {city_name}? "
            "IMPORTANTE: includi SOLO eventi che si svolgono ESATTAMENTE in data "
            f"{formatted_date} (YYYY-MM-DD: {iso_date}). "
            "Per ogni evento, includi il campo 'event_date' con formato YYYY-MM-DD. "
            "Se la data di un evento è incerta o mancante, NON includere quell'evento. "
            "Metti status='yes' SOLO se almeno un evento valido con data certa rimane. "
            "Se status='yes', inserisci gli eventi nel campo events "
            "includendo: orari, location, tipo, dettagli rilevanti quando disponibili "
            "e event_date (YYYY-MM-DD). "
            "Altrimenti metti status='no' e events=[]. "
        )

    def _extract_payload(self, raw: Any, target_date: date | None = None) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            LOGGER.warning("Unexpected payload type from Exa Answer: %s", type(raw))
            return None

        candidate = raw
        for key in ("answer", "output", "response", "data"):
            if isinstance(candidate.get(key), dict):
                candidate = candidate[key]
                break

        status = str(candidate.get("status", "")).lower()
        events = candidate.get("events", [])

        if not isinstance(events, list):
            events = []

        if target_date is not None:
            valid_events = self._filter_events_by_date(events, target_date)

            if status == "yes" and not valid_events:
                LOGGER.info("Status was 'yes' but no valid events remain after filtering")
                status = "no"

            events = valid_events

        return {"status": status, "events": events}

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

            lines.append(f"🕒 {time} – {title} ({event_type})")
            lines.append(f"📍 {location}")
            if details:
                lines.append(f"ℹ️ {details}")
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
                EXA_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
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
            "(correct any typos, e.g., 'parm a' -> 'Parma'). "
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
