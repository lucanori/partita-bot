---
status: completed
created_at: 2026-03-04
files_edited: [partita_bot/scheduler.py, tests/test_scheduler.py]
rationale: Fix morning scheduler interval so startup before the window schedules same-day notifications instead of skipping a day
supporting_docs: []
---

# Summary of changes

- Corrected the morning scheduler interval calculation to schedule the next run for the same day when the bot starts before the notification window, and only shift to the next day when past the window.
- Extracted the interval logic into a testable function and added unit coverage for before-window, in-window, and after-window scenarios.
- Made notification hours configurable via environment variables (NOTIFICATION_START_HOUR, NOTIFICATION_END_HOUR) with validation and safe fallbacks to defaults.
- Switched scheduler to a single daily run at window start (deep sleep outside the window) and added onboarding send when a user sets cities within the notification window.
- Hardened container/runtime: Dockerfile runs as non-root (uid/gid 1000) and docker-compose applies no-new-privileges, cap_drop ALL, read-only rootfs with tmpfs /tmp, and non-root user.

# Technical reasoning

- Previous logic always advanced to “tomorrow at start hour” whenever outside the window, so a start at 02:12 UTC with an 08:00–10:00 window skipped the entire day. This caused the absence of the automatic notification cycle despite pending events.
- The new calculation distinguishes three cases: before start hour (schedule today at start), inside window (15-minute cadence), after end hour (schedule tomorrow at start). A 15-minute minimum guard is preserved.
- Tests pin deterministic UTC timestamps to assert the interval seconds for each boundary condition, preventing regressions in scheduling.
- Notification window hours now honor environment overrides with range and ordering validation, preventing misconfiguration from breaking scheduling.

# Impact assessment

- Ensures deployments starting before the window (common after restarts) still dispatch the same-day morning notifications.
- No behavior change for in-window cadence or post-window scheduling; weekly blocked-user job untouched.
- Queue processing and notification deduplication remain unchanged.
- Operators can test different notification windows via compose overrides without code changes; invalid values fall back to defaults to avoid outages.
- New onboarding send ensures users who register during the window get same-day notifications; deep sleep reduces CPU outside the window; container now runs least-privileged.

# Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term`
