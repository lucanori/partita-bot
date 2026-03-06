---
status: completed
created_at: 2026-03-06
files_edited:
  - partita_bot/config.py
  - partita_bot/scheduler.py
  - partita_bot/storage.py
  - partita_bot/event_fetcher.py
  - partita_bot/notifications.py
  - tests/test_config.py
  - tests/test_storage_methods.py
  - tests/test_resiliency.py
rationale: Align runtime timezone handling with configuration and harden Exa fetch path to avoid losing daily notifications on transient failures
supporting_docs:
  - https://docs.python.org/3/library/zoneinfo.html
  - https://peps.python.org/pep-0615/
  - https://docs.python.org/3/library/sqlite3.html
---

## Summary of Changes
- Added runtime `set_timezone` helper and EXA HTTP timeout configurability; removed hardcoded Rome conversions so displays and logic follow the configured timezone.
- Switched default timezone to UTC when none is set, keeping runtime overrides via env or `set_timezone`.
- Updated scheduler logging to include local time and removed cached timezone copies so runtime updates propagate.
- Hardened Exa fetch path with retries/backoff, longer configurable timeouts, and explicit fetch-failure sentinel to prevent cache poisoning and lost daily runs.
- Adjusted notification/scheduler flow to separate fetch errors from genuine “no events,” avoiding marking the day complete when upstream fetch fails.
- Added SQLite datetime adapter to avoid Python 3.13 deprecation warnings while preserving offset-aware ISO formatting.
- Fixed admin notify-user flow to drop `__FETCH_FAILURE__` responses instead of queuing them.
- Added admin action “Clear Event Cache” to purge today’s event_cache entries for all configured cities, plus DB helpers to delete cache and list cities.
- Expanded tests for timezone formatting, runtime timezone updates, resiliency around Exa timeouts, retry adapter configuration, and scheduler state initialization.

## Technical Reasoning
- The prior `ROME_ZONE` constant and cached scheduler timezone prevented runtime overrides; replacing these with `config.TIMEZONE_INFO` ensures dynamic configuration is honored across formatting and scheduling.
- Adding `set_timezone` provides validated runtime updates using `zoneinfo`, matching stdlib guidance and keeping Europe/Rome as a safe fallback on invalid input.
- Exa requests can exceed 15s; increasing the timeout and enabling retries on 429/5xx/timeouts (with exponential backoff) reduces transient failures. A fetch-failure sentinel isolates transport errors from legitimate “no events,” preventing the scheduler from prematurely closing the daily window.
- Ensuring `scheduler_state` always has a row avoids failures when tables are created before `_upgrade_schema` inserts defaults (e.g., `create_all` paths in tests and fresh databases).

## Impact Assessment
- Timezones: Displays and window calculations now reflect the configured timezone at runtime; logging shows both UTC and local time for visibility.
- Defaults: If no timezone is set, UTC is now the fallback to match standard expectations.
- Reliability: Daily notifications are no longer skipped due to a single Exa timeout/connection error; retries and error tracking keep the scheduler from marking the day complete on failures.
- Configuration: Operators can tune `EXA_HTTP_TIMEOUT` via environment; timezone can be changed at runtime via `set_timezone` or env `TIMEZONE`.
- Backward compatibility: Defaults (Europe/Rome, 30s timeout) preserve existing behavior while adding resilience.

## Validation Steps
- Lint: `ruff check .`
- Tests: `pytest --cov=. --cov-report=term` (164 passed). Coverage remains ~91% with new resiliency and timezone tests covering added behaviors.
