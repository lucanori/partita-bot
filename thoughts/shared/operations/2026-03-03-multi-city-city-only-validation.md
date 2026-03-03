---
status: completed
created_at: 2026-03-03
files_edited:
  - partita_bot/storage.py
  - partita_bot/event_fetcher.py
  - partita_bot/bot.py
  - partita_bot/notifications.py
  - partita_bot/admin.py
  - templates/admin.html
  - tests/test_admin.py
  - tests/test_bot_handlers.py
  - tests/test_bot_module.py
  - tests/test_scheduler.py
  - thoughts/shared/operations/2026-03-03-multi-city-city-only-validation.md
rationale: Enforced city-only locations with Exa validation, added multi-city support (up to 3), prevented blocked/inaccessible users from triggering Exa queries, and updated admin UI display. Updated blocked recheck to send/delete a silent test message.
supporting_docs: []
---

## Summary of changes

- Added multi-city storage (`user_cities`) with migration and Exa-backed city classification cache.
- Bot city setup now accepts up to 3 comma-separated cities, validates each as a real city via Exa, and rejects non-city inputs.
- Notification grouping uses only active + access-allowed users and deduplicates Exa fetches per normalized city; users without cities are skipped.
- Admin flows and display reflect multi-city lists; manual notify/test use all configured cities.
- Blocked recheck sends a silent "test-message" and deletes it on success before unblocking.

## Technical reasoning

- Normalizing and caching classifications reduces Exa cost; grouping by normalized city prevents duplicate fetches across users and variants.
- Enforcing max 3 cities keeps payloads and UX simple while avoiding region/country ambiguity.
- Skipping blocked/access-denied users avoids paying Exa for unreachable recipients.

## Impact assessment

- Schema changes introduce new tables; existing single-city users are migrated automatically.
- Notification volume per user can increase (multiple cities) but Exa calls remain deduped per city per date.

## Validation steps

- Ruff: `ruff check .`
- Tests: `pytest --maxfail=1` (83 passed).
