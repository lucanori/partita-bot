from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

import partita_bot.config as config
from partita_bot.event_fetcher import EventFetcher
from partita_bot.notifications import process_notifications
from partita_bot.storage import Database

LOGGER = logging.getLogger(__name__)
scheduler_logger = logging.getLogger("apscheduler")
scheduler_logger.setLevel(logging.DEBUG if config.DEBUG else logging.WARNING)

TIMEZONE = config.TIMEZONE_INFO


def create_scheduler() -> MatchScheduler:
    db = Database()
    fetcher = EventFetcher(db)
    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "misfire_grace_time": 15 * 60,
            "coalesce": True,
        },
    )

    def calculate_next_interval() -> float:
        current_utc = datetime.now(tz=ZoneInfo("UTC"))
        local_time = current_utc.astimezone(TIMEZONE)

        if config.NOTIFICATION_START_HOUR <= local_time.hour < config.NOTIFICATION_END_HOUR:
            return 15 * 60

        tomorrow = local_time.date() + timedelta(days=1)
        next_run = datetime(
            year=tomorrow.year,
            month=tomorrow.month,
            day=tomorrow.day,
            hour=config.NOTIFICATION_START_HOUR,
            tzinfo=TIMEZONE,
        ).astimezone(ZoneInfo("UTC"))

        seconds_until_next = (next_run - current_utc).total_seconds()
        return max(seconds_until_next, 15 * 60)

    def check_and_send_notifications() -> None:
        current_utc = datetime.now(tz=ZoneInfo("UTC"))
        local_time = current_utc.astimezone(TIMEZONE)
        LOGGER.info("[%s] Running automatic notification cycle", current_utc.isoformat())

        if not (config.NOTIFICATION_START_HOUR <= local_time.hour < config.NOTIFICATION_END_HOUR):
            LOGGER.debug(
                "Outside notification window (local time %s)",
                local_time.strftime("%Y-%m-%d %H:%M"),
            )
            return

        last_run = db.get_scheduler_last_run()
        if last_run and last_run.astimezone(TIMEZONE).date() == local_time.date():
            LOGGER.debug("Notifications already dispatched today")
            return

        users = db.get_all_users()
        summary = process_notifications(
            users=users,
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_message,
            local_time=local_time,
        )

        LOGGER.info(
            "Notifications sent: %s, no events: %s, already notified: %s",
            summary["notifications_sent"],
            summary["no_events"],
            summary["already_notified"],
        )

        if summary["notifications_sent"] or summary["no_events"]:
            db.update_scheduler_last_run()

    def dynamic_schedule() -> None:
        check_and_send_notifications()
        interval = calculate_next_interval()
        scheduler.add_job(
            dynamic_schedule,
            "date",
            run_date=datetime.now(tz=ZoneInfo("UTC")) + timedelta(seconds=interval),
            id="morning_notifications",
            replace_existing=True,
        )
        LOGGER.debug("Next notification check scheduled in %.1f hours", interval / 3600)

    scheduler.add_job(
        dynamic_schedule,
        "date",
        run_date=datetime.now(tz=ZoneInfo("UTC")),
        id="morning_notifications",
    )

    return MatchScheduler(scheduler)


class MatchScheduler:
    def __init__(self, scheduler: BackgroundScheduler) -> None:
        self._scheduler = scheduler

    def start(self) -> None:
        LOGGER.info("Starting morning scheduler")
        self._scheduler.start()

    def stop(self) -> None:
        LOGGER.info("Stopping scheduler")
        self._scheduler.shutdown()
        LOGGER.info("Scheduler stopped")


LOGGER.info("scheduler.py loaded: exporting create_scheduler")
