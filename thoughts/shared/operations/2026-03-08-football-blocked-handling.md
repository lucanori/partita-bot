---
status: completed
created_at: 2026-03-08
files_edited:
  - partita_bot/event_fetcher.py
  - partita_bot/bot.py
  - partita_bot/custom_bot.py
  - partita_bot/storage.py
  - tests/test_event_fetcher.py
  - tests/test_resiliency.py
rationale:
  - Make football-data.org integration resilient to timeouts and propagate errors correctly.
  - Prevent recursion and mark users blocked when Telegram returns Forbidden.
supporting_docs: []
---

## Summary of changes

- Switched football-data.org endpoint to HTTPS, enabled retries for GET calls, and treated football-data exceptions as errors without caching negative results; football-data flow now returns status + events so scheduler sees failures when all sources error.
- Updated tests to cover football-data error propagation alongside Exa failures.
- Hardened Telegram blocked-user handling: error handler short-circuits on Forbidden and marks the user blocked; sending layer returns a blocked marker for detection; blocked detection now catches the new marker.

## Technical reasoning

- Previous retry adapter excluded GET so football-data timeouts were never retried; using HTTPS avoids redirects and network blocks.
- Returning status tuples prevents caching empty results on transport errors and surfaces a FETCH_FAILURE only when all sources fail.
- Explicit Forbidden handling prevents reply recursion and ensures blocked users are marked immediately, aligning queue processing with blocked detection.

## Impact assessment

- Football match notifications are less likely to be lost silently; cache is not polluted by transient failures.
- Blocked users are marked promptly, reducing repeated failures and noise.
- No behavioral change for successful flows; negative cache still written only on confirmed no-result responses.

## Validation steps

- `ruff check .`
- `pytest --maxfail=1` (203 tests passed, ~87% coverage)
- Docker bake/compose not run in this pass; recommend running per CONTRIBUTING on next cycle.
