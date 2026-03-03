---
status: completed
created_at: 2026-03-03
files_edited:
  - .github/CONTRIBUTING.md
  - .gitignore
  - README.md
  - run_bot.py
  - wsgi.py
  - pyproject.toml
  - partita_bot/__init__.py
  - partita_bot/admin.py
  - partita_bot/bot.py
  - partita_bot/bot_manager.py
  - partita_bot/config.py
  - partita_bot/custom_bot.py
  - partita_bot/event_fetcher.py
  - partita_bot/notifications.py
  - partita_bot/scheduler.py
  - partita_bot/storage.py
  - tests/conftest.py
  - tests/test_admin.py
  - tests/test_bot_entrypoints.py
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
  - thoughts/shared/status/2026-03-03-preexisting-exa-refactor-state.md
  - admin.py (deleted)
  - bot.py (deleted)
  - bot_manager.py (deleted)
  - config.py (deleted)
  - custom_bot.py (deleted)
  - scheduler.py (deleted)
  - storage.py (deleted)
rationale:
  - Reorganize backend code from flat root layout into a coherent Python package.
  - Preserve root entrypoints for operational compatibility while reducing root clutter.
  - Validate Exa behavior on a real historical city/date check requested by user.
supporting_docs:
  - https://exa.ai/docs/reference/answer
  - .github/CONTRIBUTING.md
  - README.md
---

## Summary of changes

- Moved core backend modules into `partita_bot/` package.
- Kept `run_bot.py` and `wsgi.py` as root entrypoints and updated imports to package paths.
- Updated internal imports across package modules and tests.
- Updated Exa query/schema behavior in `partita_bot/event_fetcher.py` to improve structured event extraction for explicit dates.
- Updated documentation to reflect the package-based architecture.
- Added contribution workflow requirements in `CONTRIBUTING.md` (lint + tests must pass; structural changes must be documented).

## Technical reasoning

- Package layout (`partita_bot/`) reduces coupling to cwd/root imports and improves maintainability.
- Keeping root entrypoints stable avoids breaking Docker/Gunicorn command conventions.
- Requiring `events` in output schema and clarifying query instructions improves odds of useful structured results for dated lookups.

## Impact assessment

- Runtime behavior is preserved (bot/admin startup paths remain unchanged from operator perspective).
- Test suite now runs against package imports and passes end-to-end.
- Root is cleaner and closer to standard Python project organization.

## Validation steps

- `ruff check .` → pass
- `pytest --cov=. --cov-report=term-missing` → pass (61 passed, 90% coverage)
- Functional user-requested check:
  - Query city `Parma`, date `27/02/2026` via `EventFetcher.fetch_event_message`
  - Result observed: notification text with at least one event (Parma-Cagliari match details)
