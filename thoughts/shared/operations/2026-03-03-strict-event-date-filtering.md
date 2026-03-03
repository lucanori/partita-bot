---
status: completed
created_at: 2026-03-03
files_edited:
  - partita_bot/event_fetcher.py
  - tests/test_event_fetcher.py
  - thoughts/shared/operations/2026-03-03-strict-event-date-filtering.md
rationale: Prevent incorrect notifications by enforcing exact target-date matching for Exa events and dropping events with missing/uncertain dates.
supporting_docs: []
---

## Summary of changes

- Added strict `event_date` requirement to the Exa event output schema.
- Updated Exa event prompt to request only exact-date events, exclude uncertain dates, and include `event_date` in `YYYY-MM-DD`.
- Implemented server-side filtering that drops events with missing `event_date` or mismatched dates.
- Added normalization behavior where `status=yes` with zero valid events is treated and cached as `status=no` with empty events.
- Expanded tests to cover wrong-date, missing-date, mixed-date, and all-filtered scenarios.

## Technical reasoning

- Structured date validation avoids false positives caused by broad retrieval and inferred dates from free-text fields.
- Server-side filtering is mandatory because model outputs can violate instructions.

## Impact assessment

- Users only receive events proven to match the requested date.
- Some previous loosely matched events are now intentionally dropped.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term` (99 passed)
