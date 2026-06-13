---
status: completed
created_at: 2026-06-12
files_edited:
  - .env.example
  - .github/CONTRIBUTING.md
  - README.md
  - partita_bot/config.py
  - partita_bot/event_fetcher.py
  - pyproject.toml
  - tests/test_event_fetcher.py
  - tests/test_resiliency.py
  - substrate/traces/operations/2026-06-12-exa-deep-reasoning-migration.md
rationale:
  - Align the Exa search step with Exa's 2026 replacement path for deprecated research-style workflows.
  - Remove deprecated request parameters and raise the default timeout to match deep-reasoning latency.
  - Preserve the existing Answer-based gate and classification flow because `/answer` remains supported and already matches the repository design.
supporting_docs:
  - https://exa.ai/docs/changelog/may-2026-api-deprecations
  - https://exa.ai/docs/reference/search-api-guide-for-coding-agents
  - https://exa.ai/docs/reference/answer
  - https://exa.ai/docs/reference/agent-api-guide
  - thoughts/shared/operations/2026-03-04-exa-search-grounded-events-and-links.md
---

## Summary of changes

- Updated Exa event-detail retrieval in `partita_bot/event_fetcher.py` to use `/search` with `type: "deep-reasoning"` and removed deprecated `useAutoprompt`.
- Increased the default `EXA_HTTP_TIMEOUT` from 30 to 60 seconds, updated tests and repository docs, and refreshed Exa-related metadata to describe the current Answer + Search workflow accurately.

## Technical reasoning

- The repository never called deprecated `/research`, so the lowest-risk migration path was to keep `/answer` for yes/no gate checks and classification while upgrading the existing `/search` step to Exa's recommended `deep-reasoning` mode.
- `/agent` was not adopted because it is beta, async, requires polling and beta headers, and would add unnecessary complexity to a synchronous fetch path that already works with schema-validated `/search` responses.
- `deep-reasoning` has materially higher documented latency than `deep`, so the default timeout was raised to avoid turning valid slow responses into fetch failures.

## Impact assessment

- Runtime behavior remains structurally the same: football-data fallback, Answer gate/classification, cache semantics, and cost tracking are unchanged.
- Search requests now use the newer reasoning mode and no longer send a deprecated Exa parameter.
- Operators can still override the timeout through `EXA_HTTP_TIMEOUT`, but new installs inherit a safer default for deep-reasoning traffic.

## Validation steps

- Verified repository context and prior Exa history by reading `README.md`, `.github/CONTRIBUTING.md`, `partita_bot/event_fetcher.py`, `partita_bot/config.py`, `.env.example`, `tests/test_event_fetcher.py`, `tests/test_resiliency.py`, and relevant legacy operation records under `thoughts/shared/operations/`.
- Reviewed diffs and final file contents for all modified files.
- Ran `ruff check .`.
- Ran `pytest --cov=. --cov-report=term`.
- Ran `docker bake`.
- Ran `docker compose -f docker-compose.local.yml up -d --build`.
- Ran `docker compose -f docker-compose.local.yml logs --tail 200`.
- Ran `docker compose -f docker-compose.local.yml down`.
- Ran a live Exa compatibility check with `.venv/bin/python` calling `EventFetcher._call_exa_search("Roma", target_date)` against the real API key from local env; the deep-reasoning request returned structured data with an `events` key and 7 events in about 14.9 seconds.
- Ran a live end-to-end fetch test with `.venv/bin/python` calling `EventFetcher.fetch_event_message("Roma", target_date)` on an in-memory database with football-data disabled; it returned a non-empty message with source links in about 21.9 seconds.
- Ran `security-review-specialist`; no vulnerabilities were reported, and no review file was generated.
