---
status: completed
created_at: 2026-03-03
files_edited:
  - partita_bot/event_fetcher.py
  - tests/test_event_fetcher.py
  - thoughts/shared/operations/2026-03-03-cache-revalidation-for-legacy-event-dates.md
rationale: Ensure previously cached legacy or mismatched events cannot still be delivered after strict date filtering rollout.
supporting_docs: []
---

## Summary of changes

- Added cache-side date revalidation in `fetch_event_message` before sending cached events.
- If cached `status=yes` contains only invalid events (missing/wrong `event_date`), cache is rewritten to `status=no` with empty events and no message is sent.
- If cached payload is mixed, only valid exact-date events are kept and cache is rewritten with filtered events.
- Introduced shared helper `_filter_events_by_date` used for both fresh Exa responses and cached entries.
- Added tests for legacy cached payloads without dates, wrong-date cached payloads, and mixed cached payloads.

## Technical reasoning

- Existing caches may contain old entries generated before strict schema enforcement.
- Revalidating cached payloads avoids incorrect notifications without waiting for cache expiration.

## Impact assessment

- First read of legacy cache may downgrade cache to `no` or trim events.
- Subsequent reads are consistent with strict date policy.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term` (102 passed)
