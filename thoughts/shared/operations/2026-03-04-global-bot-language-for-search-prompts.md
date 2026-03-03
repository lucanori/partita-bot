---
status: completed
created_at: 2026-03-04
files_edited:
  - partita_bot/config.py
  - partita_bot/event_fetcher.py
  - .env.example
  - tests/test_event_fetcher.py
  - tests/test_config.py
  - thoughts/shared/operations/2026-03-04-global-bot-language-for-search-prompts.md
rationale: Added a single global language setting for search prompts so all users share one bot language and query behavior stays cache-friendly.
supporting_docs: []
---

## Summary of changes

- Added `BOT_LANGUAGE` config sourced from environment with default `English`.
- Updated Exa search prompt builder to instruct response in `BOT_LANGUAGE`.
- Kept gate and classification prompts in English.
- Added `BOT_LANGUAGE` to `.env.example`.
- Extended tests for config reload/default and search prompt language behavior.

## Technical reasoning

- A global language avoids per-user language fan-out that could multiply query patterns and cache fragmentation.
- Search prompt remains deterministic while still configurable for self-host deployments.

## Impact assessment

- No schema or DB migration changes.
- Operators can switch response language by setting one env var.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term` (115 passed)
