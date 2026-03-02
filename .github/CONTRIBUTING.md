# Developer Guide

## Project Structure

```
partita-bot/
├── admin.py           # Flask admin interface with auth and user management
├── bot.py             # Main bot implementation with async handlers
├── bot_manager.py     # Singleton bot instance management
├── config.py          # Environment variables configuration
├── custom_bot.py      # Custom Bot class with sync message support
├── fetcher.py         # Match fetching and filtering logic
├── run_bot.py         # Standalone entry point for bot service
├── scheduler.py       # Notification scheduling with APScheduler
├── storage.py         # SQLAlchemy models and database operations (users, message queue)
├── teams.yml          # Team to city mapping configuration
├── wsgi.py            # WSGI application entry point for admin interface
├── docker-compose.yml       # Production deployment configuration
├── docker-compose.local.yml # Local development configuration
├── static/                  # Static assets for admin interface
│   └── favicon.ico         # Admin panel favicon
├── templates/              # HTML templates
│   └── admin.html         # Admin interface template
└── requirements.txt        # Project dependencies
```

## Core Components

### Database (storage.py)
- SQLite database with SQLAlchemy ORM
- Tables: users, access_control, access_mode, scheduler_state, message_queue
- Handles user management and access control
- Implements message queue for reliable notifications
- Tracks both automated and manual notification timestamps
- Supports notification rate limiting
- Implements timezone-aware timestamps
- Supports both whitelist and blocklist modes

### Message Queue System
- Uses database table for persistent message storage
- Prevents Telegram API conflicts between multiple processes
- Supports admin operations through special message types
- Queue processor runs in a dedicated thread in the bot service
- Tracks message delivery status and timestamps

### Match Fetcher (fetcher.py)
- Interfaces with football-data.org API
- Caches responses to minimize API calls
- Maps teams to cities using teams.yml
- Auto-cleans old cache files
- Case-insensitive city matching

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
- Required packages in requirements.txt

### Local Development Setup
1. Clone repository
2. Create and activate virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy .env.example to .env and configure:
   ```bash
   cp .env.example .env
   ```

### Running Locally with Docker
1. Build and start using local compose file:
   ```bash
   docker compose -f docker-compose.local.yml up -d --build
   ```
2. Monitor logs:
   ```bash
   docker compose -f docker-compose.local.yml logs -f
   ```
3. Access admin interface:
   ```
   http://localhost:5000
   ```

### Running Separate Services
For improved stability, use the SERVICE_TYPE environment variable:
1. Run bot service only:
   ```bash
   SERVICE_TYPE=bot docker compose up -d
   ```
2. Run admin panel only:
   ```bash
   SERVICE_TYPE=admin docker compose up -d
   ```

### Production Deployment
1. Ensure all changes are committed and pushed
2. Deploy using production compose file:
   ```bash
   docker compose up -d
   ```

## Common Development Tasks

### Adding New Cities
1. Update teams.yml with new team-to-city mappings
2. Format: 
   ```yaml
   cities:
     milano:
       - Inter
       - Milan
   ```

### Modifying Notification Times
1. Edit config.py
2. Update NOTIFICATION_START_HOUR and NOTIFICATION_END_HOUR variables
3. Default notification window is configurable through environment variables

### Manual Notifications
1. Use admin interface "Notify" button
2. Notifications are queued in the database
3. Bot service processes the queue and sends messages
4. Respects 5-minute cooldown between notifications

### Testing Notifications
1. Use admin interface "Test Notify" button
2. Subject to same rate limiting as manual notifications
3. Check logs for delivery status
4. Verify notification timestamps

### Updating Database Schema
1. Add new columns to model classes in storage.py
2. Include upgrade logic in _upgrade_schema method
3. Ensure backward compatibility
4. Handle timezone-aware fields properly

### Adding Bot Commands
1. Create command handler in bot.py
2. Register handler in run_bot function or create_conversation_handler
3. Update conversation handlers if needed
4. Test with both new and existing users

## Debugging

### Message Queue Issues
1. Check database for pending messages
2. Verify message processing thread is running
3. Check mark_message_sent calls for successful deliveries
4. Monitor logs for queue processing exceptions

### Event Loop Issues
1. Check for multiple event loop instances
2. Verify nest_asyncio is properly initialized
3. Monitor send_message_sync operations
4. Check loop cleanup in async functions

### Notification Issues
1. Check scheduler logs for timing
2. Verify notification timestamps
3. Check rate limiting status
4. Confirm match data is being fetched
5. Verify timezone handling

### Database Issues
1. Inspect bot.db in data directory
2. Use SQLite browser for direct access
3. Check column types and constraints
4. Verify timezone-aware fields
5. Check notification timestamps

### Docker Issues
1. Check container logs
2. Verify volume mounts
3. Check SERVICE_TYPE environment variable
4. Ensure proper cleanup between builds
5. Monitor resource usage

## Best Practices

1. Always use local Docker setup for testing
2. Handle async operations carefully
3. Use timezone-aware datetime objects
4. Use the message queue for all notifications
5. Maintain backward compatibility
6. Document significant changes
7. Keep error handling consistent
8. Use proper type hints
9. Follow Flask best practices in admin
10. Manage event loops properly

## Configuration

### Environment Variables
Required in .env file:
- TELEGRAM_BOT_TOKEN: Your bot token
- ADMIN_PORT: Port for admin interface
- ADMIN_USERNAME: Admin login username
- ADMIN_PASSWORD: Admin login password
- FOOTBALL_API_TOKEN: Football data API token
- NOTIFICATION_START_HOUR: Start of notification window (default: 7)
- NOTIFICATION_END_HOUR: End of notification window (default: 9)
- SERVICE_TYPE: Can be "bot", "admin", or empty to run both

### Docker Volumes
- data/: Contains SQLite database
- static/: Static files for admin interface