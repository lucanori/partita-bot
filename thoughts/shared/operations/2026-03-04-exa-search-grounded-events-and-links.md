---
status: completed
created_at: 2026-03-04
files_edited:
  - partita_bot/event_fetcher.py
  - tests/test_event_fetcher.py
  - thoughts/shared/operations/2026-03-04-exa-search-grounded-events-and-links.md
rationale: Reduced false positives by splitting event detection into Answer gate + Search detail extraction, enforcing strict date/city/source validation, and surfacing source links in notifications.
supporting_docs:
  - https://exa.ai/docs/reference/search
---

## Summary of changes

- Replaced single-step event extraction with a two-step Exa flow:
  - Gate check via `https://api.exa.ai/answer` returning only yes/no status.
  - Structured event detail retrieval via `https://api.exa.ai/search` (`type=deep`, schema-guided).
- Added strict event validation before caching/sending:
  - exact `event_date == target_date`
  - required `source_url`
  - deterministic city-core matching (city part before comma, e.g. `parma` from `parma, italy`) against event fields.
- Extended cache revalidation to apply the same stricter rules to previously stored entries.
- Included source links in outbound notifications (`🔗 <url>` per event).
- Updated city-classification prompt to prefer canonical `City, Country` output.

## Technical reasoning

- The gate+search split limits expensive detail extraction when no events likely exist.
- Structured deep search plus strict backend filters mitigates wrong-date and wrong-city hallucinations.
- Mandatory source URL ensures each surfaced event remains traceable.

## Impact assessment

- Fewer false positives are delivered to users.
- Some previously accepted but weakly grounded events are now discarded.
- Notifications now contain source links for manual verification.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term`
- Result: 112 tests passed, 90% total coverage.
