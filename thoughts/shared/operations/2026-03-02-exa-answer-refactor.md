---
status: completed
created_at: 2026-03-02
files_edited:
  - .env.example
  - .github/CONTRIBUTING.md
  - README.md
  - admin.py
  - bot.py
  - bot_manager.py
  - config.py
  - custom_bot.py
  - event_fetcher.py
  - notifications.py
  - pyproject.toml
  - requirements.txt
  - run_bot.py
  - scheduler.py
  - storage.py
  - tests/conftest.py
  - tests/test_admin.py
  - tests/test_bot_handlers.py
  - tests/test_bot_manager.py
  - tests/test_bot_module.py
  - tests/test_config.py
  - tests/test_custom_bot.py
  - tests/test_event_fetcher.py
  - tests/test_run_bot_helpers.py
  - tests/test_scheduler.py
  - tests/test_scheduler_module.py
  - tests/test_storage_cache.py
  - tests/test_storage_methods.py
  - tests/test_wsgi.py
  - fetcher.py (deleted)
  - teams.yml (deleted)
rationale:
  - Replace legacy football-data integration with Exa Answer endpoint and city/day cache.
  - Fan-out one city query result to all users in that city.
  - Introduce linting and broad backend tests to improve reliability during destructive refactor.
supporting_docs:
  - https://exa.ai/docs/reference/answer
  - https://exa.ai/docs/reference/error-codes.md
  - https://exa.ai/docs/reference/rate-limits.md
  - .github/CONTRIBUTING.md
---

## Summary of changes

- Replaced the old football fetch pipeline (`fetcher.py` + `teams.yml`) with a new Exa Answer integration in `event_fetcher.py`.
- Added city-grouped notification coordination in `notifications.py` so scheduler and admin flows perform one query per city per day and then fan-out messages by user.
- Extended `storage.py` with persistent `event_cache` support keyed by normalized city/date.
- Updated runtime modules (`admin.py`, `scheduler.py`, `bot.py`, `run_bot.py`) to use the new event flow and updated user-facing messaging from match-only to event-focused notifications.
- Added test/lint tooling (`pyproject.toml`) and a broad backend unit suite under `tests/`.
- Updated documentation and env template to remove football API references and document Exa usage.

## Technical reasoning

1. **Single query per city/day**: grouping users by normalized city before fetching events avoids repeated API calls and keeps costs bounded.
2. **Structured Exa responses**: `outputSchema` is enforced in the request and normalized before formatting to reduce ambiguity between "no events" and detailed events.
3. **Persistent cache**: DB cache allows re-use across scheduler/admin paths and process restarts.
4. **Reliability and regression control**: tests were added for admin flow, scheduler logic, bot handlers, storage methods, fetcher behavior, and run-bot queue helpers.

## Impact assessment

- **Functional impact**: the bot no longer depends on predefined team mappings and now supports broader city events.
- **Operational impact**: Exa API key is required for production event lookups.
- **Risk**: warnings remain for some legacy SQLAlchemy datetime patterns and unclosed sqlite resources in tests; they do not block execution but should be cleaned up in a follow-up maintenance pass.

## Validation steps

- `ruff check .` → pass
- `pytest --cov=. --cov-report=term-missing` → pass
- Current measured coverage after refactor: **87%**
