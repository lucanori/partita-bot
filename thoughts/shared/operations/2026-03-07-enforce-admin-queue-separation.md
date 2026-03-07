---
status: completed
created_at: 2026-03-07
files_edited:
  - .github/CONTRIBUTING.md
  - README.md
  - docker-compose.yml
  - docker-compose.local.yml
  - partita_bot/config.py
  - partita_bot/storage.py
  - run_bot.py
  - partita_bot/admin.py
  - tests/test_admin.py
  - tests/test_run_bot_helpers.py
  - tests/test_storage_methods.py
  - wsgi.py
  - tests/test_wsgi.py
rationale: Enforce admin queue as always-on backend-only path and strip secrets from the admin frontend deployment
supporting_docs: []
---

## Summary of changes
- Hardcoded the admin queue rollout (`USE_ADMIN_QUEUE`) so admin operations always use the dedicated `admin_queue` table; removed legacy sentinel fallback and env flag.
- Simplified admin enqueue helpers and runtime worker: backend thread now continuously processes `admin_queue`; legacy messages are only handled for cleanup.
- Updated docker compose definitions so the admin service no longer receives Telegram or Exa credentials; backend retains required secrets.
- Refreshed documentation and sample env to remove the flag and clarify that the admin frontend needs only DB access.

## Technical reasoning
- Eliminating the flag removes configuration drift and guarantees that the admin UI remains a pure frontend with no access to external API credentials.
- Keeping migration logic ensures any lingering legacy admin operations are lifted into `admin_queue` automatically.
- Removing secrets from the admin service reduces exposure surface and aligns deployment defaults with the intended architecture.

## Impact assessment
- Admin-triggered actions now always flow through the backend worker; no runtime toggle is needed.
- Operators deploy admin and bot containers with distinct env scopes: only the bot needs Telegram/Exa tokens.
- Existing databases with sentinel admin operations will be migrated once to `admin_queue`; normal operation continues via the dedicated queue.

## Validation steps
- `ruff check .`
- `pytest --cov=. --cov-report=term`
