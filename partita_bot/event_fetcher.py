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
                },
                "required": ["title", "time"],
            },
        },
    },
    "required": ["status", "events"],
}


class EventFetcher:
    def __init__(self, db: Database, http_client: requests.Session | None = None):
        self.db = db
        self.session = http_client or requests.Session()

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
                return self._format_event_message(city, target_date, events)
            return None

        payload = self._call_exa(city, target_date)
        if not payload:
            return None

        status = str(payload.get("status", "")).lower()
        events = payload.get("events") or []
        self.db.save_event_cache(city, target_date, status, events)

        if status != "yes" or not events:
            return None

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
            return self._extract_payload(data)
        except requests.RequestException as exc:  # pragma: no cover - network
            LOGGER.error("Exa Answer request failed: %s", exc)
            return None
        except ValueError as exc:
            LOGGER.error("Exa Answer response could not be decoded: %s", exc)
            return None

    def _build_query(self, city: str, target_date: date) -> str:
        formatted_date = target_date.strftime("%d/%m/%Y")
        city_name = city.strip() or "la città"
        part_a = (
            "Rispondi in italiano. Oggi "
            f"{formatted_date} ci sarà una partita di calcio o un altro evento "
            f"rilevante a {city_name}? Per la data {formatted_date} (passata o futura) descrivi "
            "la situazione e collega il racconto alla squadra della città, se possibile. "
        )
        part_b = (
            'Se status="yes" fornire almeno un evento nel campo events; '
            'se non ci sono eventi scrivi status="no" e events=[]. '
            "Per ogni risultato fornisci orari, location, tipo e dettagli rilevanti."
        )
        return part_a + part_b

    def _extract_payload(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            LOGGER.warning("Unexpected payload type from Exa Answer: %s", type(raw))
            return None

        candidate = raw
        for key in ("answer", "output", "response", "data"):
            if isinstance(candidate.get(key), dict):
                candidate = candidate[key]
                break

        return {
            "status": candidate.get("status", ""),
            "events": candidate.get("events", []),
        }

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
