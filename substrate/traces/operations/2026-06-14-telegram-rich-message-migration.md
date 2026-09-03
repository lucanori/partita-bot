---
status: completed
created_at: 2026-06-14
updated_at: 2026-09-03
files_edited:
  - .github/CONTRIBUTING.md
  - README.md
  - partita_bot/admin.py
  - partita_bot/bot.py
  - partita_bot/custom_bot.py
  - partita_bot/event_fetcher.py
  - partita_bot/notifications.py
  - partita_bot/rich_text.py
  - partita_bot/scheduler.py
  - partita_bot/storage.py
  - pyproject.toml
  - requirements.txt
  - run_bot.py
  - templates/admin.html
  - tests/conftest.py
  - tests/test_bot_handlers.py
  - tests/test_custom_bot.py
  - tests/test_event_fetcher.py
  - tests/test_notifications_multicity.py
  - tests/test_resiliency.py
  - tests/test_rich_text.py
  - tests/test_run_bot_helpers.py
  - tests/test_scheduler.py
  - tests/test_storage_methods.py
  - tests/test_scheduler_module.py
rationale: Migrate the bot to current Telegram rich-text capabilities so notifications and queued messages can carry entities, parse modes, and link preview options, then extend delivery to one rich message for text up to 32,768 characters while remaining backward compatible with legacy rows.
supporting_docs:
  - https://core.telegram.org/bots/api#rich-messages
  - https://core.telegram.org/bots/api#sendrichmessage
  - https://core.telegram.org/bots/api#inputrichmessage
  - https://core.telegram.org/bots/api#messageentity
  - https://core.telegram.org/bots/api#formatting-options
  - https://docs.python-telegram-bot.org/en/stable/index.html
  - https://docs.python-telegram-bot.org/en/v22.8/telegram.bot.html#telegram.Bot.do_api_request
  - https://pypi.org/project/python-telegram-bot/
---

# Summary of changes

Added a rich-text delivery layer for Telegram messages, upgraded `python-telegram-bot` to `22.8`, and migrated event notifications from plain strings to structured rich messages with entities, source links, and queue-persisted formatting metadata. Admin custom messages now accept either plain text or a JSON rich-message payload, while legacy queued rows continue to work unchanged. The regression suite now includes dedicated rich-text coverage for JSON ingestion, UTF-16 entity offsets, queue persistence, and admin behavior.

## Technical reasoning

- Telegram rich messages are now better expressed through explicit entities and `LinkPreviewOptions` than through plain text or fragile Markdown escaping, especially when future AI-generated content may need to control formatting safely.
- The repository previously flattened event data into a single string before queueing. That lost Telegram formatting semantics and made it impossible for the sender to use newer Bot API capabilities. Adding queue metadata columns preserved backward compatibility while allowing richer delivery.
- Internally generated notifications now prefer entities over `parse_mode`, avoiding the `parse_mode` plus `entities` conflict in Telegram delivery. Admin JSON payloads still allow `parse_mode` for operator convenience, but entities deliberately take precedence when both are provided.
- The new rich-text builder computes entity offsets in UTF-16 code units so emoji-heavy messages remain valid for Telegram entity parsing.

## Impact assessment

- Event notifications are now more compact and readable in Telegram, with bold headings, blockquote-style details, clickable source labels, and disabled previews to reduce visual noise.
- The bot worker, scheduler, onboarding flow, admin custom-message path, and queue processor now share the same rich-message transport path, reducing drift between manual and automated deliveries.
- Existing message rows remain deliverable because plain text is still stored in the original `message` column and rich metadata is additive.
- Repository documentation now reflects the new `rich_text.py` module, PTB `22.8`, and the admin JSON payload capability.

## Validation steps

- Reviewed modified code and documentation files directly after implementation.
- Ran `ruff check .`.
- Ran `pytest --cov=. --cov-report=term` with `247` passing tests and `88%` total coverage.
- Ran `docker bake`.
- Ran `docker compose -f docker-compose.local.yml up -d --build`.
- Ran `docker compose -f docker-compose.local.yml logs --tail 200`.
- Ran `docker compose -f docker-compose.local.yml down`.
- Ran a `security-review-specialist` review over all session-modified files; no meaningful vulnerabilities were reported and no review file was written under `substrate/traces/reviews/`.

## Update on 2026-09-03

### Summary of changes

Messages of 4,097 through 32,768 Unicode code points now use Telegram Bot API `sendRichMessage` as one message. Short messages keep the existing `sendMessage` path, and the queue no longer deletes old pending rows during startup.

### Technical reasoning

- Telegram still limits `sendMessage` to 4,096 characters. The 32,768-character limit belongs to the newer `sendRichMessage` method, so raising a local constant on the old method would not fix delivery.
- `python-telegram-bot` 22.8 does not expose a typed rich-message method. The sender uses its supported `do_api_request` escape hatch without changing dependencies.
- The long-message path converts persisted UTF-16 entity ranges to escaped Rich HTML. It supports bold, italic, text links, and blockquotes. Invalid, crossing, nested non-formatting, or unsupported entities fall back atomically to escaped plain text.
- The sender never splits or truncates content. Text over 32,768 code points fails locally without a Telegram call.
- Telegram `BadRequest` responses and local permanent failures terminalize the queue row. Rate limits, timeouts, network errors, and unexpected failures remain retryable.
- Removing the startup purge lets legacy pending rows reach the new transport after deployment. Pending rows are ordered by creation time and ID, and a retryable failure stops the current batch to preserve order.

### Impact assessment

- The five reported production rows between 4,160 and 7,123 characters can be retried by the worker and sent through `sendRichMessage` without a database migration or manual rewrite.
- Short messages retain their entities, parse mode, message ID handling, and link-preview settings. Telegram does not expose `link_preview_options` on `sendRichMessage`, so long messages cannot use that option.
- Permanent delivery failures no longer remain in the active retry loop. The existing schema records them as processed with no Telegram message ID.
- A live Telegram canary was not possible without a designated test chat. Production rendering and the raw endpoint response remain deployment checks.

### Validation steps

- Reviewed the cumulative diff from `27302a47324eee33f241bdac2e0901ff1fa8b0b1` to `ceb92a6` and read every changed production file.
- Ran `ruff check .`; it passed.
- Ran `pytest --cov=. --cov-report=term`; all 348 tests passed with 90% total coverage. The two main changed modules each reached 95% coverage.
- Ran `docker bake`; it passed.
- Built and started the local Compose stack with explicit fake credentials, inspected 200 log lines, and shut it down. The admin service started normally. The bot reached database and worker startup, then Telegram rejected the intentionally fake token.
- Ran the hybrid quality gate; it passed.
- Ran a focused security review over Rich HTML conversion, URL handling, raw API payload construction, logging, and queue behavior; the verdict was clear with no blocking finding.
