import logging
import os
import threading

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

import partita_bot.config as config
from partita_bot.admin import run_admin_interface
from partita_bot.bot_manager import get_bot
from partita_bot.storage import Database

# Configure logging based on DEBUG setting
logging_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging_level
)
logger = logging.getLogger(__name__)

# Control httpx logging
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.DEBUG if config.DEBUG else logging.WARNING)

db = Database()

WAITING_FOR_CITY = 1

# Message templates
MSG_UNAUTHORIZED = "Mi dispiace, non hai accesso a questo bot. Contatta l'amministratore."
MSG_WELCOME_NEW = (
    "Benvenuto! Per iniziare, usa il pulsante 'Imposta Città' per selezionare la tua città."
)
MSG_WELCOME_BACK = (
    "Bentornato!\nLa tua città attuale è {city}\n\nUsa il pulsante sotto per modificare la città."
)
MSG_CITY_PROMPT = "Per favore, invia il nome della città (es. Roma, Milano, Napoli):"
MSG_CITY_SET = (
    "Ho impostato la tua città a {city}.\n"
    "Riceverai notifiche ogni giorno tra le {start_hour}:00 e le {end_hour}:00 "
    "(CET) se ci sono eventi nella tua città!"
)


def get_main_keyboard():
    """Get the main keyboard with City button"""
    return ReplyKeyboardMarkup([["🏙 Imposta Città"]], resize_keyboard=True)


async def check_access(update: Update) -> bool:
    """Check if user has access to the bot"""
    user_id = update.effective_user.id
    access_granted = db.check_access(user_id)
    logger.debug(f"Access check for user {user_id}: {access_granted}")
    return access_granted


async def handle_unauthorized(update: Update):
    """Handle unauthorized users"""
    await update.message.reply_text(MSG_UNAUTHORIZED, reply_markup=ReplyKeyboardRemove())
    logger.warning(f"Unauthorized access attempt from user {update.effective_user.id}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command"""
    if not await check_access(update):
        await handle_unauthorized(update)
        return

    user_id = update.effective_user.id
    username = update.effective_user.username
    user = db.get_user(user_id)

    if user:
        logger.info(f"Returning user: {user_id} ({username})")
        await update.message.reply_text(
            MSG_WELCOME_BACK.format(city=user.city), reply_markup=get_main_keyboard()
        )
    else:
        logger.info(f"New user: {user_id} ({username})")
        await update.message.reply_text(MSG_WELCOME_NEW, reply_markup=get_main_keyboard())


async def start_city_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the city input conversation"""
    if not await check_access(update):
        await handle_unauthorized(update)
        return ConversationHandler.END

    await update.message.reply_text(MSG_CITY_PROMPT, reply_markup=ReplyKeyboardRemove())
    return WAITING_FOR_CITY


async def set_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the user's city"""
    if not await check_access(update):
        await handle_unauthorized(update)
        return ConversationHandler.END

    city = update.message.text.strip()
    user_id = update.effective_user.id
    username = update.effective_user.username

    logger.info(f"Setting city for user {user_id} to {city}")
    db.add_user(user_id, username, city)

    await update.message.reply_text(
        MSG_CITY_SET.format(
            city=city,
            start_hour=config.NOTIFICATION_START_HOUR,
            end_hour=config.NOTIFICATION_END_HOUR,
        ),
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def handle_invalid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle invalid input in conversation"""
    logger.debug(f"Invalid input from user {update.effective_user.id}: {update.message.text}")
    await update.message.reply_text(
        "Operazione annullata. Usa i pulsanti sotto per riprovare.",
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def show_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main keyboard to the user"""
    if not await check_access(update):
        await handle_unauthorized(update)
        return

    await update.message.reply_text(
        "Usa il pulsante sotto per impostare la tua città.", reply_markup=get_main_keyboard()
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors during message processing"""
    error = context.error
    logger.error(f"Exception while handling an update: {error}", exc_info=context.error)

    try:
        if update and update.effective_message:
            user_id = update.effective_user.id
            logger.error(f"Error for user {user_id}: {str(error)}")

            await update.effective_message.reply_text(
                "Si è verificato un errore. Usa /start per ricominciare.",
                reply_markup=get_main_keyboard(),
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}", exc_info=True)


def create_conversation_handler():
    """Create the conversation handler for city setup"""
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🏙 Imposta Città$"), start_city_input),
        ],
        states={
            WAITING_FOR_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_city)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("keyboard", show_keyboard),
            MessageHandler(filters.ALL, handle_invalid_input),
        ],
    )


def run_bot(bot_instance=None):
    """Initialize and run the bot"""
    if bot_instance is None:
        logger.info("No bot instance provided, initializing new one")
        bot_instance = get_bot(config.TELEGRAM_BOT_TOKEN)
    else:
        logger.info("Using provided bot instance")

    # Add command handlers
    bot_instance.app.add_handler(CommandHandler("start", start))
    bot_instance.app.add_handler(CommandHandler("keyboard", show_keyboard))

    # Add conversation handler
    city_conv_handler = create_conversation_handler()
    bot_instance.app.add_handler(city_conv_handler)

    # Add error handler
    bot_instance.app.add_error_handler(error_handler)

    # Don't start scheduler here, it's now in run_bot.py

    # Start bot polling
    logger.info("Starting bot polling")
    bot_instance.app.run_polling(allowed_updates=Update.ALL_TYPES)


def start_admin_interface():
    """Start the admin interface in appropriate mode"""
    if config.DEBUG:
        logger.info("Starting admin interface in debug mode")
        admin_thread = threading.Thread(target=run_admin_interface)
        admin_thread.daemon = True
        admin_thread.start()
    else:
        logger.info("Starting admin interface with Gunicorn")
        gunicorn_cmd = (
            f"gunicorn --bind 0.0.0.0:{config.ADMIN_PORT} "
            "--workers 2 --threads 4 --access-logfile - "
            "--error-logfile - wsgi:application"
        )
        admin_thread = threading.Thread(target=lambda: os.system(gunicorn_cmd))
        admin_thread.daemon = True
        admin_thread.start()


def main():
    """Main function to start the bot"""
    try:
        # Check if we're being imported by a WSGI server
        import sys

        is_imported = "gunicorn" in sys.modules or any("wsgi" in arg.lower() for arg in sys.argv)

        # Initialize bot first, before any threads
        get_bot(config.TELEGRAM_BOT_TOKEN)
        logger.info("Bot initialized successfully")

        # Start admin interface in a thread (unless being imported by WSGI)
        if not is_imported:
            logger.info("Starting admin interface in separate thread")
            start_admin_interface()
        else:
            logger.info("Running under WSGI, not starting admin interface")

        # Start bot polling (unless being imported by WSGI)
        if not is_imported:
            logger.info("Starting bot polling")
            run_bot()
        else:
            logger.info("Running under WSGI, not starting bot polling")
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
