---
status: completed
created_at: 2026-03-03
files_edited:
  - partita_bot/storage.py
  - partita_bot/event_fetcher.py
  - partita_bot/bot.py
  - partita_bot/admin.py
  - templates/admin.html
  - tests/test_city_classification.py
  - tests/test_admin.py
  - tests/test_bot_handlers.py
  - tests/test_bot_module.py
  - tests/test_scheduler.py
  - thoughts/shared/operations/2026-03-03-city-classification-ttl-and-canonical.md
rationale: Added canonical city handling with 365-day TTL cache, strict city-only validation, and admin control to clear classification cache.
supporting_docs: []
---

## Summary of changes

- Extended city classification cache with canonical name and 365-day TTL; added admin route/button to clear the cache.
- Classification via Exa now returns `is_city` and `canonical_name`; bot saves and displays canonical cities, correcting typos.
- Set-city flow enforces city-only, max 3, using canonical normalized names; notifications unaffected except for dedup using canonical keys.
- Added tests for canonical handling, TTL expiry, cache reset, and typo correction.

## Technical reasoning

- Canonical normalization prevents duplicates and fixes user typos; TTL avoids stale classifications while keeping long-lived cache.
- Admin clear enables fast recovery if bad classifications occur.

## Impact assessment

- Schema change adds canonical_name column; migration handled on startup.
- Exa usage unchanged in volume (one call per unseen/expired city key) but returns canonical to store.

## Validation steps

- Ruff: `ruff check .`
- Tests: `pytest --maxfail=1` (95 passed).
