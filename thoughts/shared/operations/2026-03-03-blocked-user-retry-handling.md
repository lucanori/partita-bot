---
status: completed
created_at: 2026-03-03
files_edited:
  - partita_bot/admin_operations.py
  - run_bot.py
  - partita_bot/storage.py
  - partita_bot/scheduler.py
  - partita_bot/notifications.py
  - partita_bot/admin.py
  - templates/admin.html
  - partita_bot/custom_bot.py
  - tests/test_run_bot_helpers.py
  - tests/test_storage_methods.py
  - tests/test_scheduler_module.py
  - tests/test_scheduler.py
  - tests/test_admin.py
  - tests/test_custom_bot.py
rationale:
  - Stop infinite queue retries for blocked Telegram users.
  - Persist and expose blocked-user state in admin UI.
  - Add recurring weekly recheck flow for previously blocked users.
supporting_docs:
  - .github/CONTRIBUTING.md
  - https://core.telegram.org/bots/api
---

## Summary of changes

- Added blocked-user error classification and handling so failed deliveries with `Forbidden + blocked` mark both user status and queue message as processed.
- Added database support for blocked status metadata (`blocked_at`, `last_block_status_check_at`) and async weekly recheck logic.
- Added scheduler job (`weekly_blocked_recheck`) that enqueues admin recheck operations.
- Updated admin route/button and table to show blocked state and last block-check timestamp.
- Updated notification flow to skip users currently marked as blocked.
- Updated tests to cover queue failure behavior, weekly recheck scheduling/processing, storage transitions, and admin rendering.

## Technical reasoning

- Queue retries should be retained only for transient failures; blocked-user failures are terminal until user unblocks bot.
- Reusing queue-based admin operations keeps Telegram I/O in bot process and avoids coupling scheduler directly to bot client lifecycle.
- Weekly probing checks only blocked users, reduces noise, and updates status in place without deleting user records.
- Returning `(success, error)` from bot send allows deterministic upstream behavior while keeping backward handling in `run_bot` for bool stubs.

## Impact assessment

- Reduces noisy repeated log spam for blocked users.
- Prevents message queue buildup on permanently blocked recipients.
- Improves observability in admin panel for operator actions.
- Keeps notification delivery for active users unaffected.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term`
- Result: all checks passed, 66 tests passed, coverage reported at 91%.
