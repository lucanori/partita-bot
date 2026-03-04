---
status: completed
created_at: 2026-03-04
files_edited:
  - partita_bot/notifications.py
  - tests/test_notifications_multicity.py
  - tests/test_bot_handlers.py
  - thoughts/shared/status/2026-03-04-onboarding-on-immediate-send.md
rationale: Prevent onboarding notifications from being skipped for users with multiple cities when the first city has no events.
supporting_docs:
  - thoughts/shared/status/2026-03-04-onboarding-on-immediate-send.md
---

## Summary of changes

- Adjusted notification fan-out logic so users are not marked as notified when a city has no events, allowing subsequent cities to deliver onboarding notifications.
- Added a targeted multi-city test to ensure one notification is queued when only later cities have events.
- Updated bot handler tests to stub fetchers correctly and to pin notification window values for the “outside window” scenario.

## Technical reasoning

- In `process_notifications`, adding users to `notified_users_today` on a no-event city prevented later cities with events from sending messages. Removing that mark keeps the per-day single-send guarantee while still permitting the first eventful city to queue a message.
- The new test simulates a two-city onboarding path (first city empty, second city with events) to guard the regression.
- Bot handler fakes now expose `fetch_event_message`, and the window is monkeypatched in the outside-window test to match the intended coverage.

## Impact assessment

- Users with multiple cities now receive onboarding notifications even if earlier cities have no events.
- No change to users with a single city or to the daily scheduler flow.
- Test coverage adds a regression guard for multi-city onboarding.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term`
