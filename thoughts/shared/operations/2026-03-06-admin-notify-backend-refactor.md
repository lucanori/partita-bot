---
status: completed
created_at: 2026-03-06
files_edited: [partita_bot/admin.py, partita_bot/admin_operations.py, run_bot.py, tests/test_admin.py, tests/test_wsgi.py, wsgi.py]
rationale: Decouple admin UI from notification fetching/sending so backend worker owns Exa and delivery flows
supporting_docs: []
---

## Summary of changes
- Admin notify actions now enqueue admin-operations instead of fetching events directly from the Flask request path.
- Added notification admin operation codes and implemented backend handlers that perform fetch + queue work in the bot service.
- Removed bot initialization side effects from the WSGI entrypoint and refreshed tests to reflect the new architecture.

## Technical reasoning
- Moving Exa fetching and Telegram delivery into the bot worker avoids blocking the admin HTTP worker and centralizes notification logic alongside existing queue processing.
- Admin operations reuse the message queue with dedicated operation identifiers, ensuring idempotent handling and clearer separation between frontend triggers and backend execution.
- WSGI no longer initializes the bot to prevent token conflicts and to keep the admin service purely frontend/auth.

## Impact assessment
- Admin UI remains synchronous only for queuing; all heavy work is deferred to the bot worker. This reduces request latency and prevents gunicorn timeouts seen in logs.
- Notification logic for both bulk and single-user paths is now consistent and leverages existing cooldowns and last-notification tracking in the worker.
- Tests now assert queueing semantics, protecting against regressions that would reintroduce frontend-side fetching.

## Validation steps
- `ruff check .`
- `pytest --cov=. --cov-report=term`
- `npx markdownlint-cli "**/*.md" --config .markdownlint.json --ignore-path .markdownlintignore --dot --fix`
