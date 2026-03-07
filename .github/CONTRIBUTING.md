# Developer Guide

## Project Structure

```text
partita-bot/
├── .env.example             # Sample environment variables file
├── .gitignore
├── Dockerfile               # Docker build instructions
├── docker-compose.yml       # Production deployment composition
├── docker-compose.local.yml # Local compose configuration for iteration
├── partita_bot/             # Package containing core application logic
│   ├── __init__.py
│   ├── admin.py             # Flask admin helpers and view handlers
│   ├── bot.py               # Telegram handler definitions and conversations
│   ├── bot_manager.py       # Singleton guard for the bot instance
│   ├── config.py            # Environment configuration and constants
│   ├── custom_bot.py        # Sync-friendly helper over python-telegram-bot
│   ├── event_fetcher.py     # Exa Answer integration and schema enforcement
│   ├── notifications.py     # City grouping, cooldown tracking, and queue helpers
│   ├── scheduler.py         # APScheduler job definitions that trigger notifications
│   └── storage.py           # SQLAlchemy models for users, queue, cache, and metadata
├── run_bot.py               # Entrypoint that boots the bot service, scheduler, queue worker
├── wsgi.py                  # WSGI callable used to serve the admin interface via Gunicorn/Flask
├── templates/               # Flask view templates (kept at repo root for Jinja lookup)
│   └── admin.html           # Admin dashboard layout
├── static/                  # Static assets served by Flask
│   └── favicon.ico
├── tests/                   # pytest suite covering the package and entrypoints
├── pyproject.toml           # Build, lint, and test configuration (primary tooling)
└── requirements.txt         # Pin-compatible dependency bundle for pip installs
```

Core modules now live inside `partita_bot/`, while `run_bot.py` and `wsgi.py` remain root-level entrypoints to start the bot and serve the admin interface. Keep templates and static assets at the repository root so Flask can discover them without additional path hacks.

## Contribution Workflow

1. After any code change you must run `ruff check .`, `pytest --cov=. --cov-report=term`, `docker bake`, `docker compose -f docker-compose.local.yml up -d --build`, `docker compose -f docker-compose.local.yml logs --tail 200` and if you find no errors, you can stop the local environment with `docker compose -f docker-compose.local.yml down`.
2. Structural changes (packages, module paths, service entrypoints, etc.) must be reflected in documentation (`README.md` and `CONTRIBUTING.md`) so engineers and AI agents can follow the new layout.
3. never write any comments in the code. we have a strict no-comment policy.
4. Admin operations always use the dedicated `admin_queue` table. The admin service (Flask) only needs database access and does not require Telegram or Exa API credentials; the backend service handles all external API calls.

## Core Components

### Database (storage.py)

- SQLite database with SQLAlchemy ORM
- Tables: users, access_control, access_mode, scheduler_state, message_queue, admin_queue
- Handles user management and access control
- Implements message queue for reliable notifications
- Tracks both automated and manual notification timestamps
- Supports notification rate limiting
- Implements timezone-aware timestamps
- Supports both whitelist and blocklist modes

### Message Queue System

- Uses database table for persistent message storage
- Prevents Telegram API conflicts between multiple processes
- Supports admin operations through the dedicated `admin_queue` table
- Queue processor runs in a dedicated thread in the bot service
- Tracks message delivery status and timestamps

### Event Fetcher (event_fetcher.py)

- Queries the Exa Answer HTTP endpoint (`https://api.exa.ai/answer`) with a localized Italian prompt about matches/events in a city
- Enforces an `outputSchema` so the response always exposes `status` and `events` with time/location/type/details
- Normalizes and formats the structured response into a user-friendly notification text
- Persists the normalized response through the `event_cache` table so each city/date fetch runs at most once

### Notification Coordination (notifications.py)

- Groups users by normalized city so scheduler/admin flows trigger only one event fetch per city per run
- Tracks last-notification timestamps and manual cooldowns per user
- Queues event text into the message queue while updating per-user notification metadata

### Scheduler (scheduler.py)

- Uses APScheduler for reliable job execution
- Uses configurable notification window
- Sends notifications during specified hour range
- Prevents duplicate notifications same day
- Tracks last run time in database
- Queues messages in database instead of sending directly

### Bot Architecture

#### Bot Manager (bot_manager.py)

- Manages singleton bot instance across application
- Provides global access to bot instance
- Ensures consistent bot state in all components
- Tracks process and thread ownership of bot instance
- Prevents multiple processes from accessing Telegram API

#### Custom Bot (custom_bot.py)

- Extends python-telegram-bot with sync capabilities
- Implements send_message_sync for reliable notifications
- Handles event loop management for sync operations

#### Main Bot (bot.py)

- Uses python-telegram-bot v20.7
- Implements conversation flows for settings
- Manages user registration and preferences
- Contains command handlers and conversation logic
- Centralizes bot creation functions

#### Bot Runner (run_bot.py)

- Standalone entry point for bot service
- Initializes bot, scheduler, and message queue processor
- Checks for token conflicts before starting
- Processes queued messages in background thread
- Handles admin operations that require async processing

### Admin Interface (admin.py)

- Flask-based web interface
- User management with access control
- Manual notification triggers with rate limiting
- Uses message queue instead of direct Telegram API access
- Test notification support
- User activity monitoring
- Custom favicon and styling
- Proper error handling and feedback

### WSGI Application (wsgi.py)

- Production entry point for Gunicorn
- Ensures bot initialization is properly managed
- Supports running admin interface separately

## Development Setup

### Requirements

- Python 3.10+
- Docker and Docker Compose
- Recommended dependency management through `pyproject.toml` (install with `uv install --dev` or fall back to `pip install -r requirements.txt`)
