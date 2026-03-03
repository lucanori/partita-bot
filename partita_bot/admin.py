from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

import partita_bot.config as config
from partita_bot.admin_operations import (
    DELETE_SENT_LAST_HOURS,
    RECHECK_BLOCKED_USERS,
    format_admin_operation,
)
from partita_bot.event_fetcher import EventFetcher
from partita_bot.notifications import process_notifications
from partita_bot.storage import Database

LOGGER = logging.getLogger(__name__)

app = Flask(__name__, template_folder=str(Path(__file__).parent.parent / "templates"))
app.secret_key = config.FLASK_SECRET_KEY
auth = HTTPBasicAuth()
db = Database()
event_fetcher = EventFetcher(db)

users = {config.ADMIN_USERNAME: generate_password_hash(config.ADMIN_PASSWORD)}


def send_message_via_db_queue(chat_id: int, text: str) -> bool:
    return db.queue_message(telegram_id=chat_id, message=text)


@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username


@app.route("/")
@auth.login_required
def index():
    access_mode = db.get_access_mode()
    all_users = db.get_all_users()
    return render_template(
        "admin.html",
        users=all_users,
        access_mode=access_mode,
        current_mode=access_mode,
        db=db,
    )


@app.route("/set_mode", methods=["POST"])
@auth.login_required
def set_mode():
    mode = request.form.get("mode", "blocklist")
    if mode in ["whitelist", "blocklist"]:
        db.set_access_mode(mode)
    return redirect(url_for("index"))


@app.route("/toggle_access/<int:user_id>", methods=["POST"])
@auth.login_required
def toggle_access(user_id):
    mode = db.get_access_mode()
    action = request.form.get("action")

    if mode == "whitelist":
        if action == "allow":
            db.add_to_list("whitelist", user_id)
        elif action == "remove":
            db.remove_from_list("whitelist", user_id)
    else:
        if action == "block":
            db.add_to_list("blocklist", user_id)
        elif action == "unblock":
            db.remove_from_list("blocklist", user_id)

    return redirect(url_for("index"))


@app.route("/cleanup_users", methods=["POST"])
@auth.login_required
def cleanup_users():
    try:
        db.queue_message(
            telegram_id=0,
            message=format_admin_operation(RECHECK_BLOCKED_USERS),
        )
        flash("Blocked user recheck has been queued. Check back later for results.", "info")
    except Exception as exc:
        LOGGER.exception("Failed to queue cleanup operation")
        flash(f"Error during cleanup: {exc}", "error")
    return redirect(url_for("index"))


@app.route("/notify_all", methods=["POST"])
@auth.login_required
def notify_all():
    try:
        local_time = datetime.now(tz=ZoneInfo("UTC")).astimezone(config.TIMEZONE_INFO)
        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=event_fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        msg = (
            f"Notifications sent: {summary['notifications_sent']}, "
            f"No events: {summary['no_events']}, "
            f"Already notified today: {summary['already_notified']}"
        )
        flash(msg, "success" if summary["notifications_sent"] > 0 else "info")
    except Exception as exc:
        LOGGER.exception("Error in notify_all")
        flash(f"Error in notify_all: {exc}", "error")

    return redirect(url_for("index"))


@app.route("/notify_user/<int:user_id>", methods=["POST"])
@auth.login_required
def notify_user(user_id):
    user = db.get_user(user_id)
    if not user:
        flash("User not found", "error")
        return redirect(url_for("index"))

    if not db.can_send_manual_notification(user_id):
        flash(
            f"Please wait at least 5 minutes between manual notifications for user {user_id}",
            "error",
        )
        return redirect(url_for("index"))

    local_time = datetime.now(tz=ZoneInfo("UTC")).astimezone(config.TIMEZONE_INFO)
    message = event_fetcher.fetch_event_message(user.city, local_time.date())

    if not message:
        flash(f"No events found for user {user_id} in {user.city}. Notification not sent.", "info")
        return redirect(url_for("index"))

    if send_message_via_db_queue(chat_id=user_id, text=message):
        db.update_last_notification(user_id, is_manual=True)
        flash(f"Notification sent to user {user_id}", "success")
    else:
        flash("Failed to queue the notification.", "error")

    return redirect(url_for("index"))


@app.route("/test_notification/<int:user_id>", methods=["POST"])
@auth.login_required
def test_notification(user_id):
    user = db.get_user(user_id)
    if not user:
        flash("User not found", "error")
        return redirect(url_for("index"))

    if not db.can_send_manual_notification(user_id):
        flash(
            f"Please wait at least 5 minutes between manual notifications for user {user_id}",
            "error",
        )
        return redirect(url_for("index"))

    message = (
        f"🎯 Test notifiche eventi per {user.city}:\n"
        "🕒 15:00 – Evento di prova\n"
        "📍 Centro città\n\n"
        "Questo è un messaggio di test per verificare il sistema."
    )

    if send_message_via_db_queue(chat_id=user_id, text=message):
        db.update_last_notification(user_id, is_manual=True)
        flash(f"Test notification sent to user {user_id}", "success")
    else:
        flash("Failed to queue test notification.", "error")

    return redirect(url_for("index"))


@app.route("/send_custom_message/<int:user_id>", methods=["POST"])
@auth.login_required
def send_custom_message(user_id):
    user = db.get_user(user_id)
    if not user:
        flash("User not found", "error")
        return redirect(url_for("index"))

    custom_text = request.form.get("message_text", "").strip()
    if not custom_text:
        flash("Message text cannot be empty", "error")
        return redirect(url_for("index"))

    if send_message_via_db_queue(chat_id=user_id, text=custom_text):
        flash(f"Custom message queued for user {user_id}", "success")
    else:
        flash("Failed to queue custom message.", "error")

    return redirect(url_for("index"))


@app.route("/delete_user_pending/<int:user_id>", methods=["POST"])
@auth.login_required
def delete_user_pending(user_id):
    user = db.get_user(user_id)
    if not user:
        flash("User not found", "error")
        return redirect(url_for("index"))

    deleted = db.delete_pending_messages_for_user_last_n_hours(user_id, hours=24)
    if deleted > 0:
        flash(
            f"Deleted {deleted} pending message(s) for user {user_id} from the last 24 hours",
            "success",
        )
    else:
        flash(f"No pending messages found for user {user_id} from the last 24 hours", "info")

    return redirect(url_for("index"))


@app.route("/delete_user_sent_last_hour/<int:user_id>", methods=["POST"])
@auth.login_required
def delete_user_sent_last_hour(user_id):
    user = db.get_user(user_id)
    if not user:
        flash("User not found", "error")
        return redirect(url_for("index"))

    try:
        db.queue_message(
            telegram_id=0,
            message=format_admin_operation(DELETE_SENT_LAST_HOURS, str(user_id), "1"),
        )
        flash(
            f"Delete sent messages operation queued for user {user_id} (last 1 hour). "
            "The bot will process this shortly.",
            "info",
        )
    except Exception as exc:
        LOGGER.exception("Failed to queue delete sent messages operation")
        flash(f"Error queueing delete operation: {exc}", "error")

    return redirect(url_for("index"))


def run_admin_interface() -> None:
    app.run(host="0.0.0.0", port=config.ADMIN_PORT)
