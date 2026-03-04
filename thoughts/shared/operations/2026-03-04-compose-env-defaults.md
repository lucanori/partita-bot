---
status: completed
created_at: 2026-03-04
files_edited: [".env.example", "docker-compose.yml", "thoughts/shared/status/2026-03-04-local-compose-env-updates.md"]
rationale: Make DEBUG optional with sane defaults and document optional env vars
supporting_docs: []
---

## Summary of changes
- Added optional settings section to `.env.example` with commented defaults for DEBUG, TIMEZONE, notification window, BOT_LANGUAGE, and ADMIN_PORT.
- Updated `docker-compose.yml` to pull DEBUG, TIMEZONE, BOT_LANGUAGE, and notification window values from environment with defaults so DEBUG is optional by default.

## Technical reasoning
- `config.py` already defaults `DEBUG` to false when unset; using `${DEBUG:-false}` in compose makes the variable optional while preserving the default.
- Adding optional vars to `.env.example` documents configurable runtime parameters without forcing overrides.

## Impact assessment
- Containers inherit defaults when optional variables are absent; existing behavior remains unless values are provided.
- Admin service still binds to 5086 as configured; APPDATA/UID/GID substitutions remain required for volume permissions.

## Validation steps
- Ran `npx markdownlint-cli "**/*.md" --config .markdownlint.json --ignore-path .markdownlintignore --dot --fix` (markdown-only changes validated; no code/tests run because scope limited to compose/env docs).
