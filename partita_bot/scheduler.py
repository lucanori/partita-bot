from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

import partita_bot.config as config
from partita_bot.admin_operations import RECHECK_BLOCKED_USERS, format_admin_operation
from partita_bot.event_fetcher import EventFetcher
from partita_bot.notifications import process_notifications
from partita_bot.storage import Database

LOGGER = logging.getLogger(__name__)
scheduler_logger = logging.getLogger("apscheduler")
scheduler_logger.setLevel(logging.DEBUG if config.DEBUG else logging.WARNING)

TIMEZONE = config.TIMEZONE_INFO


def calculate_next_interval(
    current_utc: datetime,
    start_hour: int,
    end_hour: int,
    timezone: ZoneInfo,
) -> float:
    local_time = current_utc.astimezone(timezone)

    if start_hour <= local_time.hour < end_hour:
        return 15 * 60

    if local_time.hour < start_hour:
        next_run = datetime(
            year=local_time.year,
            month=local_time.month,
            day=local_time.day,
            hour=start_hour,
            tzinfo=timezone,
        ).astimezone(ZoneInfo("UTC"))
    else:
        tomorrow = local_time.date() + timedelta(days=1)
        next_run = datetime(
            year=tomorrow.year,
            month=tomorrow.month,
            day=tomorrow.day,
            hour=start_hour,
            tzinfo=timezone,
        ).astimezone(ZoneInfo("UTC"))

    seconds_until_next = (next_run - current_utc).total_seconds()
    return max(seconds_until_next, 15 * 60)


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
        current_utc = datetime.now(tz=ZoneInfo("UTC"))
        interval = calculate_next_interval(
            current_utc=current_utc,
            start_hour=config.NOTIFICATION_START_HOUR,
            end_hour=config.NOTIFICATION_END_HOUR,
            timezone=TIMEZONE,
        )
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

    def enqueue_weekly_blocked_recheck() -> None:
        message = format_admin_operation(RECHECK_BLOCKED_USERS)
        try:
            with Database() as queue_db:
                queue_db.queue_message(telegram_id=0, message=message)
            LOGGER.info("Scheduled weekly blocked user recheck")
        except Exception as exc:
            LOGGER.error("Failed to schedule blocked recheck: %s", exc)

    scheduler.add_job(
        enqueue_weekly_blocked_recheck,
        "cron",
        day_of_week="mon",
        hour=0,
        minute=0,
        id="weekly_blocked_recheck",
        replace_existing=True,
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
