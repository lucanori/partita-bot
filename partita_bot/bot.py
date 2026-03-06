import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

import partita_bot.config as config
from partita_bot.admin import run_admin_interface
from partita_bot.bot_manager import get_bot
from partita_bot.event_fetcher import EventFetcher
from partita_bot.notifications import process_notifications
from partita_bot.storage import Database, User

logging_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging_level,
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _log_converter(seconds: float | None) -> time.struct_time:
    return config.timezone_converter(seconds).timetuple()


logging.Formatter.converter = staticmethod(_log_converter)
logger = logging.getLogger(__name__)

httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.DEBUG if config.DEBUG else logging.WARNING)

db = Database()

WAITING_FOR_CITY = 1

MSG_UNAUTHORIZED = "Mi dispiace, non hai accesso a questo bot. Contatta l'amministratore."
MSG_UNAUTHORIZED_WHITELIST = (
    "Il bot è in modalità whitelist. Contatta l'amministratore per essere abilitato. "
    "Il tuo ID: {user_id}"
)
MSG_WELCOME_NEW = (
    "Benvenuto! Per iniziare, usa il pulsante 'Imposta città' per selezionare fino a 3 città."
)
MSG_WELCOME_BACK = (
    "Bentornato!\nLe tue città attuali: {cities}\n\nUsa il pulsante sotto per modificare le città."
)
MSG_CITY_PROMPT = "Invia fino a 3 città separate da virgola (solo città):"
MSG_CITY_SET = (
    "Ho impostato le tue città:\n"
    "{cities}\n"
    "Riceverai notifiche ogni giorno tra le {start_hour}:00 e le {end_hour}:00 "
    "({timezone}) se ci sono eventi nelle tue città!"
)
MSG_CITY_REJECTED = "Solo città sono consentite. '{location}' non è una città. Riprova."
MSG_CITY_TOO_MANY = "Puoi impostare massimo 3 città. Riprova."


def get_main_keyboard():
    return ReplyKeyboardMarkup([["🏙 Imposta città"]], resize_keyboard=True)


async def check_access(update: Update) -> bool:
    user_id = update.effective_user.id
    access_granted = db.check_access(user_id)
    logger.debug(f"Access check for user {user_id}: {access_granted}")
    return access_granted


async def handle_access_denied(update: Update) -> bool:
    user_id = update.effective_user.id
    username = update.effective_user.username
    mode = db.get_access_mode()

    if mode == "whitelist":
        db.upsert_pending_request(user_id, username)

    if db.should_send_denial(user_id):
        if mode == "whitelist":
            await update.message.reply_text(
                MSG_UNAUTHORIZED_WHITELIST.format(user_id=user_id),
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            await update.message.reply_text(MSG_UNAUTHORIZED, reply_markup=ReplyKeyboardRemove())

    logger.warning(f"Unauthorized access attempt from user {user_id} (mode: {mode})")
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        await handle_access_denied(update)
        return

    user_id = update.effective_user.id
    username = update.effective_user.username
    user = db.get_user(user_id)

    if user:
        logger.info(f"Returning user: {user_id} ({username})")
        cities = db.get_user_cities(user_id)
        cities_str = ", ".join(c.title() for c in cities) if cities else "Nessuna"
        await update.message.reply_text(
            MSG_WELCOME_BACK.format(cities=cities_str), reply_markup=get_main_keyboard()
        )
    else:
        logger.info(f"New user: {user_id} ({username})")
        await update.message.reply_text(MSG_WELCOME_NEW, reply_markup=get_main_keyboard())


async def start_city_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        await handle_access_denied(update)
        return ConversationHandler.END

    await update.message.reply_text(MSG_CITY_PROMPT, reply_markup=ReplyKeyboardRemove())
    return WAITING_FOR_CITY


def _was_notified_today(user: User, local_date) -> bool:
    last_notification = user.last_notification
    if not last_notification:
        return False

    if last_notification.tzinfo is None:
        last_notification = last_notification.replace(tzinfo=ZoneInfo("UTC"))

    local_time = last_notification.astimezone(config.TIMEZONE_INFO)
    return local_time.date() == local_date


def _maybe_send_onboarding_notification(user_id: int) -> None:
    current_utc = datetime.now(tz=ZoneInfo("UTC"))
    local_time = current_utc.astimezone(config.TIMEZONE_INFO)

    if not (config.NOTIFICATION_START_HOUR <= local_time.hour < config.NOTIFICATION_END_HOUR):
        logger.debug("Onboarding: outside notification window, skipping immediate notification")
        return

    user = db.get_user(user_id)
    if not user:
        return

    if _was_notified_today(user, local_time.date()):
        logger.debug("Onboarding: user already notified today, skipping")
        return

    cities = db.get_user_cities(user_id)
    if not cities:
        return

    fetcher = EventFetcher(db)

    summary = process_notifications(
        users=[user],
        db=db,
        fetcher=fetcher,
        queue_message=db.queue_message,
        local_time=local_time,
    )

    if summary["notifications_sent"] > 0:
        logger.info(f"Onboarding: queued immediate notification for user {user_id}")
    elif summary["no_events"] > 0:
        logger.debug(f"Onboarding: no events for user {user_id}")


async def set_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        await handle_access_denied(update)
        return ConversationHandler.END

    text = update.message.text.strip()
    user_id = update.effective_user.id
    username = update.effective_user.username

    raw_cities = [c.strip() for c in text.split(",") if c.strip()]
    if len(raw_cities) > 3:
        await update.message.reply_text(MSG_CITY_TOO_MANY, reply_markup=get_main_keyboard())
        return ConversationHandler.END

    fetcher = EventFetcher(db)
    validated_cities = []
    for city in raw_cities:
        normalized = db.normalize_city(city)
        cached_is_city, cached_canonical = db.get_city_classification(normalized)
        if cached_is_city is True:
            canonical_to_use = cached_canonical if cached_canonical else normalized
            validated_cities.append(canonical_to_use)
        elif cached_is_city is False:
            await update.message.reply_text(
                MSG_CITY_REJECTED.format(location=city), reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END
        else:
            is_city, canonical_name = fetcher.classify_city(city)
            if is_city is None:
                await update.message.reply_text(
                    "Errore durante la verifica. Riprova più tardi.",
                    reply_markup=get_main_keyboard(),
                )
                return ConversationHandler.END
            if not is_city:
                await update.message.reply_text(
                    MSG_CITY_REJECTED.format(location=city), reply_markup=get_main_keyboard()
                )
                return ConversationHandler.END
            canonical_to_use = canonical_name if canonical_name else normalized
            validated_cities.append(canonical_to_use)

    db.add_user(user_id, username, raw_cities[0] if raw_cities else "")
    saved_cities = db.set_user_cities(user_id, validated_cities)
    cities_display = "\n".join(c.title() for c in saved_cities)

    logger.info(f"Setting cities for user {user_id}: {cities_display}")

    await update.message.reply_text(
        MSG_CITY_SET.format(
            cities=cities_display,
            start_hour=config.NOTIFICATION_START_HOUR,
            end_hour=config.NOTIFICATION_END_HOUR,
            timezone=config.TIMEZONE,
        ),
        reply_markup=get_main_keyboard(),
    )

    _maybe_send_onboarding_notification(user_id)

    return ConversationHandler.END


async def handle_invalid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Invalid input from user {update.effective_user.id}: {update.message.text}")
    await update.message.reply_text(
        "Operazione annullata. Usa i pulsanti sotto per riprovare.",
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def show_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        await handle_access_denied(update)
        return

    await update.message.reply_text(
        "Usa il pulsante sotto per impostare la tua città.", reply_markup=get_main_keyboard()
    )


async def handle_general_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        await handle_access_denied(update)
        return

    await update.message.reply_text(
        "Usa il pulsante sotto per impostare la tua città.", reply_markup=get_main_keyboard()
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🏙 Imposta città$"), start_city_input),
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
    if bot_instance is None:
        logger.info("No bot instance provided, initializing new one")
        bot_instance = get_bot(config.TELEGRAM_BOT_TOKEN)
    else:
        logger.info("Using provided bot instance")

    bot_instance.app.add_handler(CommandHandler("start", start))
    bot_instance.app.add_handler(CommandHandler("keyboard", show_keyboard))

    city_conv_handler = create_conversation_handler()
    bot_instance.app.add_handler(city_conv_handler)

    bot_instance.app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_message)
    )

    bot_instance.app.add_error_handler(error_handler)
    logger.info("Starting bot polling")
    bot_instance.app.run_polling(allowed_updates=Update.ALL_TYPES)


def start_admin_interface():
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
    try:
        import sys

        is_imported = "gunicorn" in sys.modules or any("wsgi" in arg.lower() for arg in sys.argv)
        get_bot(config.TELEGRAM_BOT_TOKEN)
        logger.info("Bot initialized successfully")
        if not is_imported:
            logger.info("Starting admin interface in separate thread")
            start_admin_interface()
        else:
            logger.info("Running under WSGI, not starting admin interface")

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
