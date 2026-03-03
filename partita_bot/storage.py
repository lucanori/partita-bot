import json
import logging
import os
from datetime import date, datetime
from types import TracebackType
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

UTC_ZONE = ZoneInfo("UTC")


def _utcnow() -> datetime:
    return datetime.now(tz=UTC_ZONE)


# Table to store pending messages for the bot to send
class MessageQueue(Base):
    __tablename__ = "message_queue"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, nullable=False)
    message = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    sent = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String, nullable=True)
    city = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    is_blocked = Column(Boolean, default=False)
    last_notification = Column(DateTime, nullable=True)
    last_manual_notification = Column(DateTime, nullable=True)


class AccessControl(Base):
    __tablename__ = "access_control"

    id = Column(Integer, primary_key=True)
    mode = Column(String, nullable=False)
    telegram_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class AccessMode(Base):
    __tablename__ = "access_mode"

    id = Column(Integer, primary_key=True)
    mode = Column(String, nullable=False, default="blocklist")
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class SchedulerState(Base):
    __tablename__ = "scheduler_state"

    id = Column(Integer, primary_key=True)
    last_run = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class EventCache(Base):
    __tablename__ = "event_cache"
    __table_args__ = (UniqueConstraint("city", "date", name="uq_event_cache_city_date"),)

    id = Column(Integer, primary_key=True)
    city = Column(String, nullable=False)
    date = Column(String, nullable=False)
    status = Column(String, nullable=False)
    events = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class Database:
    def __init__(self, database_url: str | None = None):
        if database_url:
            self.database_url = database_url
        else:
            db_path = os.path.join("data", "bot.sqlite3")
            os.makedirs("data", exist_ok=True)
            self.database_url = f"sqlite:///{db_path}"
        self.engine = create_engine(self.database_url)
        Base.metadata.create_all(self.engine)
        session_factory = sessionmaker(bind=self.engine)
        self.session = session_factory()
        self._upgrade_schema()

        if not self.session.query(AccessMode).first():
            default_mode = AccessMode(mode="blocklist")
            self.session.add(default_mode)
            self.session.commit()

    def _upgrade_schema(self):
        inspector = inspect(self.engine)

        user_columns = [col["name"] for col in inspector.get_columns(User.__tablename__)]
        if "last_notification" not in user_columns:
            with self.engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_notification DATETIME"))
        if "last_manual_notification" not in user_columns:
            with self.engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_manual_notification DATETIME"))

        if not inspector.has_table("scheduler_state"):
            SchedulerState.__table__.create(self.engine)
            with self.engine.begin() as conn:
                conn.execute(text("INSERT INTO scheduler_state (id) VALUES (1)"))
        if not inspector.has_table("event_cache"):
            EventCache.__table__.create(self.engine)

    @staticmethod
    def normalize_city(city: str) -> str:
        if not city:
            return ""
        return city.strip().casefold()

    def add_user(self, telegram_id: int, username: str, city: str) -> User:
        user = self.session.query(User).filter_by(telegram_id=telegram_id).first()
        if user:
            user.username = username
            user.city = city
        else:
            user = User(telegram_id=telegram_id, username=username, city=city)
            self.session.add(user)
        self.session.commit()
        return user

    def get_user(self, telegram_id: int) -> User:
        return self.session.query(User).filter_by(telegram_id=telegram_id).first()

    def get_all_users(self):
        return self.session.query(User).all()

    def block_user(self, telegram_id: int) -> bool:
        user = self.get_user(telegram_id)
        if user:
            user.is_blocked = True
            self.session.commit()
            return True
        return False

    def unblock_user(self, telegram_id: int) -> bool:
        user = self.get_user(telegram_id)
        if user:
            user.is_blocked = False
            self.session.commit()
            return True
        return False

    def set_access_mode(self, mode: str):
        if mode not in ["whitelist", "blocklist"]:
            raise ValueError("Mode must be either 'whitelist' or 'blocklist'")
        access_mode = self.session.query(AccessMode).first()
        access_mode.mode = mode
        self.session.commit()

    def get_access_mode(self) -> str:
        access_mode = self.session.query(AccessMode).first()
        return access_mode.mode if access_mode else "blocklist"

    def add_to_list(self, mode: str, telegram_id: int):
        if mode not in ["whitelist", "blocklist"]:
            raise ValueError("Mode must be either 'whitelist' or 'blocklist'")
        entry = AccessControl(mode=mode, telegram_id=telegram_id)
        self.session.add(entry)
        self.session.commit()

    def remove_from_list(self, mode: str, telegram_id: int):
        self.session.query(AccessControl).filter_by(mode=mode, telegram_id=telegram_id).delete()
        self.session.commit()

    def check_access(self, telegram_id: int) -> bool:
        mode = self.get_access_mode()
        if mode == "whitelist":
            return bool(
                self.session.query(AccessControl)
                .filter_by(mode="whitelist", telegram_id=telegram_id)
                .first()
            )
        else:
            return not bool(
                self.session.query(AccessControl)
                .filter_by(mode="blocklist", telegram_id=telegram_id)
                .first()
            )

    def _get_utc_now(self) -> datetime:
        """Get current UTC time with timezone info"""
        return datetime.now(tz=UTC_ZONE)

    def _ensure_timezone_aware(self, dt: datetime | None) -> datetime | None:
        """Ensure datetime is timezone aware (UTC)"""
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))

    def update_last_notification(self, telegram_id: int, is_manual: bool = False):
        """Update the last notification timestamp for a user"""
        user = self.get_user(telegram_id)
        if user:
            now = self._get_utc_now()
            user.last_notification = now
            if is_manual:
                user.last_manual_notification = now
            self.session.commit()

    def can_send_manual_notification(self, telegram_id: int, cooldown_minutes: int = 5) -> bool:
        """Check if a manual notification can be sent based on cooldown time"""
        user = self.get_user(telegram_id)
        if not user or not user.last_manual_notification:
            return True

        now = self._get_utc_now()
        last_manual = self._ensure_timezone_aware(user.last_manual_notification)
        if last_manual is None:
            return True
        assert last_manual is not None

        time_since_last = now - last_manual

        return time_since_last.total_seconds() >= cooldown_minutes * 60

    def format_last_notification(self, telegram_id: int) -> str:
        """Format the last notification time for display"""
        user = self.get_user(telegram_id)
        if user and user.last_notification:
            tz_aware = self._ensure_timezone_aware(user.last_notification)
            if tz_aware is None:
                return "Never"
            assert tz_aware is not None
            rome_time = tz_aware.astimezone(ZoneInfo("Europe/Rome"))
            return rome_time.strftime("%Y-%m-%d %H:%M:%S")
        return "Never"

    def update_scheduler_last_run(self):
        """Update the last run time of the scheduler"""
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE scheduler_state SET last_run = :now WHERE id = 1"),
                {"now": self._get_utc_now()},
            )

    def get_scheduler_last_run(self) -> datetime | None:
        """Get the last time the scheduler ran"""
        result = self.session.query(SchedulerState).first()
        if result and result.last_run:
            tz_run = self._ensure_timezone_aware(result.last_run)
            if tz_run is not None:
                return tz_run
        return None

    def get_event_cache(self, city: str, target_date: date | datetime) -> dict[str, Any] | None:
        normalized_city = self.normalize_city(city)
        if not normalized_city:
            return None

        if isinstance(target_date, datetime):
            target_date = target_date.date()

        date_key = target_date.isoformat()
        entry = (
            self.session.query(EventCache).filter_by(city=normalized_city, date=date_key).first()
        )

        if not entry:
            return None

        events = []
        if entry.events:
            try:
                events = json.loads(entry.events)
            except json.JSONDecodeError:
                logging.getLogger(__name__).warning(
                    "Invalid event cache payload for %s on %s", city, date_key
                )

        return {"status": entry.status, "events": events}

    def save_event_cache(
        self,
        city: str,
        target_date: date | datetime,
        status: str,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        normalized_city = self.normalize_city(city)
        if not normalized_city:
            return

        if isinstance(target_date, datetime):
            target_date = target_date.date()

        date_key = target_date.isoformat()
        existing = (
            self.session.query(EventCache).filter_by(city=normalized_city, date=date_key).first()
        )

        payload = json.dumps(events or [], ensure_ascii=False)

        if existing:
            existing.status = status
            existing.events = payload
        else:
            cache_entry = EventCache(
                city=normalized_city, date=date_key, status=status, events=payload
            )
            self.session.add(cache_entry)

        self.session.commit()

    def queue_message(self, telegram_id: int, message: str) -> bool:
        """Queue a message to be sent by the bot process"""
        try:
            queue_item = MessageQueue(
                telegram_id=telegram_id, message=message, created_at=self._get_utc_now()
            )
            self.session.add(queue_item)
            self.session.commit()
            logger = logging.getLogger(__name__)
            logger.info(f"Message queued for user {telegram_id}")
            return True
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error queueing message: {str(e)}")
            return False

    def get_pending_messages(self, limit: int = 10) -> list:
        """Get pending messages to be sent"""
        return (
            self.session.query(MessageQueue)
            .filter(MessageQueue.sent.is_(False))
            .order_by(MessageQueue.created_at)
            .limit(limit)
            .all()
        )

    def mark_message_sent(self, message_id: int) -> bool:
        """Mark a message as sent"""
        try:
            message = self.session.get(MessageQueue, message_id)
            if message:
                message.sent = True
                message.sent_at = self._get_utc_now()
                self.session.commit()
                return True
            return False
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error marking message as sent: {str(e)}")
            return False

    def close(self) -> None:
        """Close the session and dispose of the engine."""
        if hasattr(self, "session"):
            try:
                self.session.close()
            except Exception:
                logging.getLogger(__name__).exception("Failed to close session")
        if hasattr(self, "engine"):
            try:
                self.engine.dispose()
            except Exception:
                logging.getLogger(__name__).exception("Failed to dispose engine")

    def __enter__(self) -> "Database":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            logging.getLogger(__name__).exception("Error while closing database in __del__")

    async def remove_blocked_users(self, bot) -> dict:
        users = self.get_all_users()
        total = len(users)
        removed = 0
        errors = []
        logger = logging.getLogger(__name__)

        for user in users:
            user_id = user.telegram_id
            logger.debug(f"Checking if user {user_id} has blocked the bot")

            try:
                message = await bot.bot.send_message(
                    chat_id=user_id, text="test message, please ignore", disable_notification=True
                )

                # Message sent successfully, user has not blocked the bot
                await bot.bot.delete_message(chat_id=user_id, message_id=message.message_id)
                logger.debug(f"User {user_id} has not blocked the bot")

            except Exception as e:
                error_str = str(e).lower()

                # Check if user has blocked the bot
                if "forbidden" in error_str and "blocked" in error_str:
                    logger.info(f"Removing user {user_id} who blocked the bot")
                    self.session.delete(user)
                    removed += 1
                else:
                    logger.warning(f"Error checking user {user_id}: {str(e)}")
                    errors.append(f"User {user_id}: {str(e)}")

        # Commit changes if any users were removed
        if removed > 0:
            self.session.commit()
            logger.info(f"Removed {removed} users who blocked the bot")

        return {"total_users": total, "removed_users": removed, "errors": errors}
