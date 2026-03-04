from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import partita_bot.config as config
from partita_bot.storage import Database, User

LOGGER = logging.getLogger(__name__)
QueueFn = Callable[[int, str], bool]


def group_users_by_cities(users: list[User], db: Database) -> dict[str, list[tuple[User, str]]]:
    groups: dict[str, list[tuple[User, str]]] = {}
    for user in users:
        if user.is_blocked:
            continue
        if not db.check_access(user.telegram_id):
            continue
        cities = db.get_user_cities(user.telegram_id)
        if not cities:
            LOGGER.debug("Skipping user %s with no cities", user.telegram_id)
            continue
        for city in cities:
            groups.setdefault(city, []).append((user, city))
    return groups


def _was_notified_today(user: User, local_date: date) -> bool:
    last_notification = user.last_notification
    if not last_notification:
        return False

    if last_notification.tzinfo is None:
        last_notification = last_notification.replace(tzinfo=ZoneInfo("UTC"))

    local_time = last_notification.astimezone(config.TIMEZONE_INFO)
    return local_time.date() == local_date


def process_notifications(
    users: list[User],
    db: Database,
    fetcher: Any,
    queue_message: QueueFn,
    local_time: datetime,
    mark_manual: bool = False,
) -> dict[str, int]:
    summary = {"notifications_sent": 0, "no_events": 0, "already_notified": 0}
    city_groups = group_users_by_cities(users, db)
    local_date = local_time.date()
    notified_users_today: set[int] = set()

    for normalized_city, user_city_pairs in city_groups.items():
        city_label = normalized_city.title() or "la tua città"
        message = fetcher.fetch_event_message(city_label, local_date)

        for user, _ in user_city_pairs:
            if user.telegram_id in notified_users_today:
                continue

            if _was_notified_today(user, local_date):
                summary["already_notified"] += 1
                notified_users_today.add(user.telegram_id)
                continue

            if not message:
                summary["no_events"] += 1
                continue

            if not queue_message(user.telegram_id, message):
                LOGGER.error("Failed to queue event notification for %s", user.telegram_id)
                continue

            db.update_last_notification(user.telegram_id, is_manual=mark_manual)
            summary["notifications_sent"] += 1
            notified_users_today.add(user.telegram_id)

    return summary
