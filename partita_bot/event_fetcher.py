from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import partita_bot.config as config
from partita_bot.storage import Database

LOGGER = logging.getLogger(__name__)
EXA_ANSWER_ENDPOINT = "https://api.exa.ai/answer"
EXA_SEARCH_ENDPOINT = "https://api.exa.ai/search"
FOOTBALL_DATA_ENDPOINT = "https://api.football-data.org/v4/matches"

FETCH_FAILURE = "__FETCH_FAILURE__"

QUERY_TYPE_FOOTBALL = "football"
QUERY_TYPE_GENERAL = "general"
QUERY_TYPE_FOOTBALL_DATA = "football_data"

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

TEAM_CITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "country": {"type": "string"},
    },
    "required": ["city"],
}


class EventFetcher:
    def __init__(self, db: Database, http_client: requests.Session | None = None):
        self.db = db
        if http_client is not None:
            self.session = http_client
        else:
            self.session = requests.Session()
            retry_strategy = Retry(
                total=3,
                backoff_factor=2,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["POST", "GET"],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

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
        require_source_url: bool = True,
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
            if require_source_url and not source_url:
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

    def _dedup_key(self, event: dict[str, Any]) -> str:
        source_url = event.get("source_url", "")
        if source_url:
            return source_url.lower().strip()
        title = event.get("title", "").lower().strip()
        event_date = event.get("event_date", "").lower().strip()
        time = event.get("time", "").lower().strip()
        return f"{title}|{event_date}|{time}"

    def _merge_and_dedupe(
        self, football_events: list[dict[str, Any]], general_events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for event in football_events + general_events:
            key = self._dedup_key(event)
            if key not in seen:
                seen.add(key)
                merged.append(event)
        return merged

    def _fetch_football_data_matches(
        self, city: str, target_date: date
    ) -> tuple[str, list[dict[str, Any]]]:
        if not config.FOOTBALL_API_TOKEN:
            LOGGER.debug("FOOTBALL_API_TOKEN not set, skipping football-data.org fetch")
            return ("no", [])

        cached = self.db.get_event_cache(city, target_date, QUERY_TYPE_FOOTBALL_DATA)
        if cached:
            events = cached.get("events") or []
            valid_events = self._filter_events(events, target_date, city, require_source_url=False)
            if cached.get("status") == "yes" and valid_events:
                return ("yes", valid_events)
            if cached.get("status") == "no":
                return ("no", [])

        yesterday = target_date - timedelta(days=1)
        tomorrow = target_date + timedelta(days=1)
        params = {
            "dateFrom": yesterday.isoformat(),
            "dateTo": tomorrow.isoformat(),
        }
        headers = {"X-Auth-Token": config.FOOTBALL_API_TOKEN}

        try:
            response = self.session.get(
                FOOTBALL_DATA_ENDPOINT,
                headers=headers,
                params=params,
                timeout=(5, config.EXA_HTTP_TIMEOUT),
            )
            response.raise_for_status()
            data = response.json()
        except requests.Timeout as exc:
            LOGGER.error("Football-data request timed out: %s", exc)
            return ("error", [])
        except requests.ConnectionError as exc:
            LOGGER.error("Football-data request connection error: %s", exc)
            return ("error", [])
        except requests.RequestException as exc:
            LOGGER.error("Football-data request failed: %s", exc)
            return ("error", [])
        except ValueError as exc:
            LOGGER.error("Football-data response could not be decoded: %s", exc)
            return ("error", [])

        matches = data.get("matches", [])
        events = self._convert_football_matches_to_events(matches, target_date, city)
        valid_events = self._filter_events(events, target_date, city, require_source_url=False)

        if valid_events:
            self.db.save_event_cache(
                city, target_date, "yes", valid_events, QUERY_TYPE_FOOTBALL_DATA
            )
            return ("yes", valid_events)
        else:
            self.db.save_event_cache(city, target_date, "no", [], QUERY_TYPE_FOOTBALL_DATA)
            return ("no", [])

    def _convert_football_matches_to_events(
        self, matches: list[dict[str, Any]], target_date: date, target_city: str
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        target_city_normalized = self.db.normalize_city(target_city)

        for match in matches:
            if not isinstance(match, dict):
                continue

            home_team = match.get("homeTeam", {})
            away_team = match.get("awayTeam", {})
            home_team_name = home_team.get("name", "")
            away_team_name = away_team.get("name", "")

            if not home_team_name or not away_team_name:
                continue

            match_city = self._get_city_for_team(home_team_name)
            if not match_city:
                continue

            match_city_normalized = self.db.normalize_city(match_city)
            if match_city_normalized != target_city_normalized:
                continue

            utc_date_str = match.get("utcDate", "")
            if not utc_date_str:
                continue

            try:
                utc_dt = datetime.fromisoformat(utc_date_str.replace("Z", "+00:00"))
                local_dt = utc_dt.astimezone(config.TIMEZONE_INFO)
                match_date = local_dt.date()
                match_time = local_dt.strftime("%H:%M")
            except (ValueError, TypeError):
                continue

            if match_date != target_date:
                continue

            competition = match.get("competition", {})
            competition_name = competition.get("name", "")
            venue = match.get("venue", "")
            match_id = match.get("id", "")

            title = f"{home_team_name} vs {away_team_name}"
            details = competition_name if competition_name else "Football Match"
            source_url = f"https://www.football-data.org/matches/{match_id}" if match_id else ""

            event = {
                "title": title,
                "time": match_time,
                "location": venue if venue else home_team_name,
                "type": "Football",
                "details": details,
                "event_date": match_date.isoformat(),
                "source_url": source_url,
            }
            events.append(event)

        return events

    def _get_city_for_team(self, team_name: str) -> str | None:
        cached_city = self.db.get_team_city(team_name)
        if cached_city:
            return cached_city

        if not config.EXA_API_KEY:
            LOGGER.debug("Cannot classify team city: EXA_API_KEY missing")
            return None

        city = self._classify_team_city(team_name)
        if city:
            self.db.set_team_city(team_name, city)
        return city

    def _classify_team_city(self, team_name: str) -> str | None:
        payload = {
            "query": self._build_team_city_query(team_name),
            "outputSchema": TEAM_CITY_SCHEMA,
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
                timeout=(5, config.EXA_HTTP_TIMEOUT),
            )
            response.raise_for_status()
            data = response.json()
            self._record_cost_from_response(data, "answer")
            result = self._extract_team_city_payload(data)
            if result:
                return result.get("city")
            return None
        except requests.Timeout as exc:
            LOGGER.error("Exa team city classification request timed out: %s", exc)
            return None
        except requests.ConnectionError as exc:
            LOGGER.error("Exa team city classification request connection error: %s", exc)
            return None
        except requests.RequestException as exc:
            LOGGER.error("Exa team city classification request failed: %s", exc)
            return None
        except ValueError as exc:
            LOGGER.error("Exa team city classification response could not be decoded: %s", exc)
            return None

    def _build_team_city_query(self, team_name: str) -> str:
        return (
            f'What city is the football team "{team_name}" based in? '
            "Respond with the city name in the 'city' field "
            "and the country in the 'country' field. "
            "Be specific: return the city where the team's home stadium is located."
        )

    def _extract_team_city_payload(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            LOGGER.warning(
                "Unexpected payload type from Exa team city classification: %s", type(raw)
            )
            return None
        candidate = raw
        for key in ("answer", "output", "response", "data"):
            if isinstance(candidate.get(key), dict):
                candidate = candidate[key]
                break
        city = candidate.get("city", "")
        if city:
            return {"city": city, "country": candidate.get("country", "")}
        return None

    def _fetch_single_flow(
        self,
        city: str,
        target_date: date,
        query_type: str,
        gate_query_override: str | None = None,
        search_query_override: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        cached = self.db.get_event_cache(city, target_date, query_type)
        if cached:
            status = str(cached.get("status", "")).lower()
            events = cached.get("events") or []
            if status == "yes" and events:
                valid_events = self._filter_events(events, target_date, city)
                if valid_events:
                    if len(valid_events) != len(events):
                        self.db.save_event_cache(city, target_date, "yes", valid_events, query_type)
                    return ("yes", valid_events)
                self.db.save_event_cache(city, target_date, "no", [], query_type)
                return ("no", [])
            return (status, events)

        gate_result = self._call_exa_gate(city, target_date, gate_query_override)
        if gate_result is None:
            return ("error", [])

        gate_status = str(gate_result.get("status", "")).lower()

        if gate_status != "yes":
            self.db.save_event_cache(city, target_date, "no", [], query_type)
            return ("no", [])

        search_result = self._call_exa_search(city, target_date, search_query_override)
        if search_result is None:
            return ("error", [])

        events = search_result.get("events") or []
        valid_events = self._filter_events(events, target_date, city)

        if not valid_events:
            LOGGER.info("No valid events remain after filtering for %s on %s", city, target_date)
            self.db.save_event_cache(city, target_date, "no", [], query_type)
            return ("no", [])

        self.db.save_event_cache(city, target_date, "yes", valid_events, query_type)
        return ("yes", valid_events)

    def fetch_event_message(self, city: str, target_date: date | None = None) -> str | None:
        normalized_city = self.db.normalize_city(city)
        if not normalized_city:
            LOGGER.debug("Skipping event lookup because city is empty")
            return None

        target_date = target_date or datetime.now(config.TIMEZONE_INFO).date()

        football_data_status, football_data_events = self._fetch_football_data_matches(
            city, target_date
        )

        football_status, football_events = self._fetch_single_flow(
            city,
            target_date,
            QUERY_TYPE_FOOTBALL,
            gate_query_override=self._build_football_gate_query(city, target_date),
            search_query_override=self._build_football_search_query(city, target_date),
        )

        general_status, general_events = self._fetch_single_flow(
            city,
            target_date,
            QUERY_TYPE_GENERAL,
            gate_query_override=self._build_general_gate_query(city, target_date),
            search_query_override=self._build_general_search_query(city, target_date),
        )

        if (
            football_data_status == "error"
            and football_status == "error"
            and general_status == "error"
        ):
            return FETCH_FAILURE

        if football_status == "error":
            LOGGER.warning(
                "Football flow failed for %s on %s, using general only", city, target_date
            )
        elif general_status == "error":
            LOGGER.warning(
                "General flow failed for %s on %s, using football only", city, target_date
            )

        all_events = self._merge_and_dedupe(
            football_data_events, self._merge_and_dedupe(football_events, general_events)
        )

        if not all_events:
            return None

        return self._format_event_message(city, target_date, all_events)

    def _call_exa_gate(
        self, city: str, target_date: date, query_override: str | None = None
    ) -> dict[str, Any] | None:
        if not config.EXA_API_KEY:
            LOGGER.error("Cannot query Exa Answer because EXA_API_KEY is missing")
            return None

        payload = {
            "query": query_override or self._build_general_gate_query(city, target_date),
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
                timeout=(5, config.EXA_HTTP_TIMEOUT),
            )
            response.raise_for_status()
            data = response.json()
            self._record_cost_from_response(data, "answer")
            return self._extract_gate_payload(data)
        except requests.Timeout as exc:
            LOGGER.error("Exa Answer gate request timed out: %s", exc)
            return None
        except requests.ConnectionError as exc:
            LOGGER.error("Exa Answer gate request connection error: %s", exc)
            return None
        except requests.RequestException as exc:
            LOGGER.error("Exa Answer gate request failed: %s", exc)
            return None
        except ValueError as exc:
            LOGGER.error("Exa Answer gate response could not be decoded: %s", exc)
            return None

    def _call_exa_search(
        self, city: str, target_date: date, query_override: str | None = None
    ) -> dict[str, Any] | None:
        if not config.EXA_API_KEY:
            LOGGER.error("Cannot query Exa Search because EXA_API_KEY is missing")
            return None

        payload = {
            "query": query_override or self._build_general_search_query(city, target_date),
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
                timeout=(5, config.EXA_HTTP_TIMEOUT),
            )
            response.raise_for_status()
            data = response.json()
            self._record_cost_from_response(data, "search")
            return self._extract_search_payload(data)
        except requests.Timeout as exc:
            LOGGER.error("Exa Search request timed out: %s", exc)
            return None
        except requests.ConnectionError as exc:
            LOGGER.error("Exa Search request connection error: %s", exc)
            return None
        except requests.RequestException as exc:
            LOGGER.error("Exa Search request failed: %s", exc)
            return None
        except ValueError as exc:
            LOGGER.error("Exa Search response could not be decoded: %s", exc)
            return None

    def _build_general_gate_query(self, city: str, target_date: date) -> str:
        formatted_date = target_date.strftime("%d/%m/%Y")
        city_name = city.strip() or "the city"
        return (
            f"On {formatted_date}, will there be any relevant events such as concerts, shows, "
            f"cultural events, festivals, or other public gatherings in the following city: "
            f"{city_name}? "
            "Exclude football matches and sports events. "
            "Reply ONLY with status='yes' if there are confirmed events, otherwise status='no'. "
            "Do not include event details, only yes/no."
        )

    def _build_general_search_query(self, city: str, target_date: date) -> str:
        formatted_date = target_date.strftime("%d/%m/%Y")
        iso_date = target_date.isoformat()
        city_name = city.strip() or "the city"
        language = config.BOT_LANGUAGE
        return (
            f"Respond in {language}. "
            f"Find concerts, shows, cultural events, festivals, and other relevant public events "
            f"in {city_name} on {formatted_date} ({iso_date}). "
            "Exclude football matches and sports events. "
            "For each event, include: title, time, location, type, details, "
            f"event_date (format YYYY-MM-DD: {iso_date}), and source_url (source URL). "
            "Include ONLY events with confirmed date and valid source URL. "
            "Return events in the 'events' field of the output schema."
        )

    def _build_football_gate_query(self, city: str, target_date: date) -> str:
        formatted_date = target_date.strftime("%d/%m/%Y")
        city_name = city.strip() or "the city"
        return (
            f"On {formatted_date}, will there be any football matches (soccer games) "
            f"in the following city: {city_name}? "
            "Reply ONLY with status='yes' if there are confirmed football matches, "
            "otherwise status='no'. Do not include event details, only yes/no."
        )

    def _build_football_search_query(self, city: str, target_date: date) -> str:
        formatted_date = target_date.strftime("%d/%m/%Y")
        iso_date = target_date.isoformat()
        city_name = city.strip() or "the city"
        language = config.BOT_LANGUAGE
        return (
            f"Respond in {language}. "
            f"Find football matches (soccer games) in {city_name} on {formatted_date} "
            f"({iso_date}). "
            "For each match, include: title (team names), time, location (stadium), "
            "type (football/soccer), details (league/competition), "
            f"event_date (format YYYY-MM-DD: {iso_date}), and source_url (source URL). "
            "Include ONLY matches with confirmed date and valid source URL. "
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
                timeout=(5, config.EXA_HTTP_TIMEOUT),
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
        except requests.Timeout as exc:
            LOGGER.error("Exa city classification request timed out: %s", exc)
            return (None, "")
        except requests.ConnectionError as exc:
            LOGGER.error("Exa city classification request connection error: %s", exc)
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
