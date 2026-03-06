---
status: completed
created_at: 2026-03-06
files_edited:
  - .env.example
  - partita_bot/config.py
  - partita_bot/event_fetcher.py
  - partita_bot/storage.py
  - tests/test_city_classification.py
  - tests/test_event_fetcher.py
  - tests/test_resiliency.py
rationale: Add football-data.org match ingestion with Exa-backed team-to-city mapping, extend cache TTLs, and keep flows resilient when the football token is absent.
supporting_docs:
  - https://www.football-data.org/documentation/quickstart
---

## Summary of changes

- Added `FOOTBALL_API_TOKEN` config handling and documented it in `.env.example`, logging an informational skip when absent.
- Introduced football-data.org match ingestion in `EventFetcher`, converting matches to events using local formatting and merging with existing Exa football/general flows.
- Added Exa-backed team-to-city classification with a two-year cache to avoid repeated lookups; created `team_city_cache` table and methods in storage.
- Extended city-classification cache TTL to two years; adjusted tests and resiliency mocks to accommodate football-data GET usage.

## Technical reasoning

- Skipping when `FOOTBALL_API_TOKEN` is missing avoids hard failures for deployments focused only on general events.
- Team-to-city mapping via Exa ensures match localization without new paid calls once cached; a two-year TTL mirrors the expected stability of club locations.
- Reusing `event_cache` with a dedicated query type keeps football-data results isolated per city/date while honoring existing caching and filtering.
- Allowing football-data events without mandatory `source_url` avoids dropping valid matches while still deduplicating with available identifiers.

## Impact assessment

- Users now receive structured football matches alongside existing event notifications when the token is configured.
- Exa costs are minimized after the initial team-city classification due to long-lived caching.
- Deployments without the football token continue operating without errors.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term`
