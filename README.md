# Partita Bot

Partita Bot is a Telegram bot that keeps Italians informed about the next football match or major sporting event happening in their city by consulting Exa Answer each morning and delivering curated notifications inside the configured time window. Users choose their city via Telegram, the scheduler asks Exa about the day ahead, and notifications are queued, rate limited, and delivered reliably by the bot. The Flask admin panel provides controls for managing access, manual triggers, and user state.

## Features

- **Daily Event Signals:** Each morning the scheduler builds a localized query ("oggi DD/MM/YYYY ci sarà una partita di calcio o un evento in città X?") and asks the Exa Answer endpoint for structured event details (orari, location, tipo). Only one query per city per day is performed thanks to the DB-backed cache.
- **Message Queue System:** Reliable message delivery through a database-backed queue to prevent Telegram API conflicts.
- **User Configuration:** New users are prompted to set their city. Existing users can update their settings.
- **Admin Panel:** A simple Flask-based admin interface for managing mode settings and users (allow, block, unblock, or remove). Includes access control and flash notifications.
- **Container Separation:** Bot and admin services can run in separate containers to improve stability and prevent API conflicts.
- **Scheduler:** APScheduler is used for periodic job execution. The scheduler fetches match data on a set schedule.
- **Docker Ready:** The project is containerized using Docker. There are separate configurations for production and local development.

## Project Structure

```text
├── .env.example             # Sample environment variables file
├── .gitignore
├── Dockerfile               # Docker container build instructions
├── docker-compose.yml       # Production deployment compose (production)
├── docker-compose.local.yml # Local compose for rapid iteration
├── partita_bot/             # Python package with core modules
│   ├── __init__.py
│   ├── admin.py             # Flask admin logic and view helpers
│   ├── bot.py               # Telegram handlers, conversations, error handling
│   ├── bot_manager.py       # Singleton bot instance ownership guard
│   ├── config.py            # Environment configuration and constants
│   ├── custom_bot.py        # Sync-friendly wrapper over python-telegram-bot
│   ├── event_fetcher.py     # Exa Answer integration and schema enforcement
│   ├── notifications.py     # City grouping, cooldowns, and queue helpers
│   ├── scheduler.py         # APScheduler job definitions and triggers
│   └── storage.py           # SQLAlchemy models for users/message queue/cache
├── run_bot.py               # Entry point that boots bot, scheduler, and queue worker
├── wsgi.py                  # WSGI callable used when serving the admin interface
├── templates/               # Flask templates (kept at repo root for Jinja lookup)
│   └── admin.html           # Admin dashboard layout
├── static/                  # Static assets served by the admin interface
│   └── favicon.ico
├── tests/                   # pytest suite covering package modules and entrypoints
├── pyproject.toml           # Build, lint, and test configuration (primary toolchain)
└── requirements.txt         # Pin-compatible dependency bundle for pip installs
```

Core modules now live inside `partita_bot/`, but the root entrypoints `run_bot.py` and `wsgi.py` keep their previous paths and import from the package. Templates and static assets remain at the repository root so Flask can locate them without additional path tweaks.

## Setup and Configuration

1. **Environment Variables:**  
    Copy `.env.example` to `.env` and update the necessary variables. Common settings include:
    - `TELEGRAM_BOT_TOKEN`: Your Telegram bot token
    - `ADMIN_PORT`: Port for the admin interface
    - `ADMIN_USERNAME`/`ADMIN_PASSWORD`: Admin panel credentials
    - `NOTIFICATION_START_HOUR`/`NOTIFICATION_END_HOUR`: Notification time window
    - `EXA_API_KEY`: Bearer credential for Exa Answer, required for event detection queries
    - `SERVICE_TYPE`: Can be "bot", "admin", or empty to run both

2. **Dependencies:**  
    Dependencies are declared in `pyproject.toml` so you can use `uv` or `pip` even when pairing with `requirements.txt`. They include:
    - python-telegram-bot (v20.7)
    - Flask and Flask-HTTPAuth
    - SQLAlchemy
    - APScheduler
    - nest_asyncio (for handling nested event loops)
    - Other libraries such as requests and python-dotenv

3. **Database:**  
    The `storage.py` module handles database operations including:
    - User management
    - Message queue for reliable notifications
    - Scheduler state tracking
    - `event_cache` table: stores Exa Answer responses keyed by normalized city + date so repeated city lookups are avoided (scheduler and admin flows reuse cached data)

## Testing & Linting

- **Linting:** `ruff check .` enforces style, type hints, and formatting rules centralized in `pyproject.toml`.
- **Tests:** `pytest --cov=. --cov-report=term` runs the unit suite with coverage reporting (new tests cover config, storage cache, event fetcher, scheduler grouping, and admin notify flows).
- Both commands use the same dependency definitions declared in `pyproject.toml`, so installing with `uv install` or `pip install .[dev]` keeps tooling aligned.

## Running the Bot

### Via Docker (Production)

1. **Build and run:**

   ```bash
   docker compose up -d --build
   ```

   This uses the default `docker-compose.yml` which supports separated services.

2. **Logs:**
   Monitor logs with:

   ```bash
   docker compose logs -f
   ```

### Separate Services Deployment

For improved stability, you can run the bot and admin panel as separate services:

1. **Run bot service only:**

   ```bash
   SERVICE_TYPE=bot docker compose up -d
   ```

2. **Run admin panel only:**

   ```bash
   SERVICE_TYPE=admin docker compose up -d
   ```

### Local Development

For local testing with your latest changes without pushing to GitHub, use `docker-compose.local.yml`:

1. **Build and run locally:**

   ```bash
   docker compose -f docker-compose.local.yml up -d --build
   ```

2. **Access Admin Panel:**
   Open your browser and navigate to `http://localhost:5000` (or the port specified in your `.env`).

3. **Logs:**
   Check real-time logs:

   ```bash
   docker compose -f docker-compose.local.yml logs -f
   ```

## Contributing

Contributions are welcome. Please refer to `DEVELOPER_GUIDE.md` for guidelines on code style, testing, and Git workflow.

## License

This project is licensed under the terms found in the [LICENSE](LICENSE) file.
