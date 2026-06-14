---
status: completed
created_at: 2026-06-14
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
  - tests/test_event_fetcher.py
  - tests/test_notifications_multicity.py
  - tests/test_resiliency.py
  - tests/test_rich_text.py
  - tests/test_run_bot_helpers.py
  - tests/test_scheduler.py
  - tests/test_scheduler_module.py
rationale: Migrate the bot to current Telegram rich-text capabilities so notifications and queued messages can carry entities, parse modes, and link preview options while remaining backward compatible with legacy plain-text rows.
supporting_docs:
  - https://core.telegram.org/bots/api#rich-messages
  - https://core.telegram.org/bots/api#messageentity
  - https://core.telegram.org/bots/api#formatting-options
  - https://docs.python-telegram-bot.org/en/stable/index.html
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
