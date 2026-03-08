import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
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

import partita_bot.config as config

Base = declarative_base()

UTC_ZONE = ZoneInfo("UTC")


def _utcnow() -> datetime:
    return datetime.now(tz=UTC_ZONE)


def _adapt_datetime(dt: datetime) -> str:
    return dt.isoformat()


sqlite3.register_adapter(datetime, _adapt_datetime)


def is_user_blocked_error(error_message: str | None) -> bool:
    if not error_message:
        return False
    normalized = error_message.lower()
    return "blocked" in normalized or ("forbidden" in normalized and "blocked" in normalized)


class MessageQueue(Base):
    __tablename__ = "message_queue"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, nullable=False)
    message = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    sent = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)
    sent_message_id = Column(Integer, nullable=True)


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
    blocked_at = Column(DateTime, nullable=True)
    last_block_status_check_at = Column(DateTime, nullable=True)


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


class UserCity(Base):
    __tablename__ = "user_cities"
    __table_args__ = (UniqueConstraint("user_id", "city", name="uq_user_city"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    city = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class CityClassificationCache(Base):
    __tablename__ = "city_classification_cache"

    id = Column(Integer, primary_key=True)
    normalized_name = Column(String, unique=True, nullable=False)
    is_city = Column(Boolean, nullable=False)
    canonical_name = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=_utcnow)


class TeamCityCache(Base):
    __tablename__ = "team_city_cache"

    id = Column(Integer, primary_key=True)
    normalized_team_name = Column(String, unique=True, nullable=False)
    city = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class EventCache(Base):
    __tablename__ = "event_cache"
    __table_args__ = (
        UniqueConstraint("city", "date", "query_type", name="uq_event_cache_city_date_type"),
    )

    id = Column(Integer, primary_key=True)
    city = Column(String, nullable=False)
    date = Column(String, nullable=False)
    query_type = Column(String, nullable=False, default="general")
    status = Column(String, nullable=False)
    events = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class ExaCost(Base):
    __tablename__ = "exa_costs"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    cost = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow)


class PendingAccessRequest(Base):
    __tablename__ = "pending_access_requests"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String, nullable=True)
    first_seen = Column(DateTime, default=_utcnow)
    last_seen = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class AccessDenialLog(Base):
    __tablename__ = "access_denial_log"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    last_sent = Column(DateTime, nullable=False, default=_utcnow)


class AdminQueue(Base):
    __tablename__ = "admin_queue"

    id = Column(Integer, primary_key=True)
    operation = Column(String, nullable=False)
    payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)


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
        if "blocked_at" not in user_columns:
            with self.engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN blocked_at DATETIME"))
        if "last_block_status_check_at" not in user_columns:
            with self.engine.connect() as conn:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN last_block_status_check_at DATETIME")
                )

        queue_columns = [col["name"] for col in inspector.get_columns(MessageQueue.__tablename__)]
        if "sent_message_id" not in queue_columns:
            with self.engine.connect() as conn:
                conn.execute(text("ALTER TABLE message_queue ADD COLUMN sent_message_id INTEGER"))

        if not inspector.has_table("scheduler_state"):
            SchedulerState.__table__.create(self.engine)
        with self.engine.begin() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM scheduler_state"))
            count = result.scalar()
            if count == 0:
                conn.execute(text("INSERT INTO scheduler_state (id) VALUES (1)"))
        if not inspector.has_table("event_cache"):
            EventCache.__table__.create(self.engine)
        else:
            event_cache_columns = [
                col["name"] for col in inspector.get_columns(EventCache.__tablename__)
            ]
            unique_defs = inspector.get_unique_constraints(EventCache.__tablename__)
            has_query_type = "query_type" in event_cache_columns
            has_query_type_unique = any(
                set(constraint.get("column_names", [])) == {"city", "date", "query_type"}
                for constraint in unique_defs
            )

            needs_rebuild = False

            if not has_query_type:
                needs_rebuild = True

            if not needs_rebuild and not has_query_type_unique:
                needs_rebuild = True

            if needs_rebuild:
                with self.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE event_cache RENAME TO event_cache_old"))
                    EventCache.__table__.create(self.engine)
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO event_cache (city, date, query_type, status, "
                            "events, created_at) "
                            "SELECT city, date, COALESCE(query_type, 'general'), status, events, "
                            "created_at FROM event_cache_old"
                        )
                    )
                    conn.execute(text("DROP TABLE event_cache_old"))
            elif not has_query_type:
                with self.engine.connect() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE event_cache ADD COLUMN query_type VARCHAR "
                            "DEFAULT 'general'"
                        )
                    )
                    conn.execute(
                        text(
                            "UPDATE event_cache SET query_type = 'general' WHERE query_type IS NULL"
                        )
                    )
        if not inspector.has_table("user_cities"):
            UserCity.__table__.create(self.engine)
            self._migrate_single_city_to_multi()
        if not inspector.has_table("city_classification_cache"):
            CityClassificationCache.__table__.create(self.engine)
        else:
            cache_columns = [
                col["name"] for col in inspector.get_columns(CityClassificationCache.__tablename__)
            ]
            if "canonical_name" not in cache_columns:
                with self.engine.connect() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE city_classification_cache "
                            "ADD COLUMN canonical_name VARCHAR DEFAULT ''"
                        )
                    )
        if not inspector.has_table("exa_costs"):
            ExaCost.__table__.create(self.engine)
        if not inspector.has_table("pending_access_requests"):
            PendingAccessRequest.__table__.create(self.engine)
        if not inspector.has_table("access_denial_log"):
            AccessDenialLog.__table__.create(self.engine)
        if not inspector.has_table("team_city_cache"):
            TeamCityCache.__table__.create(self.engine)
        if not inspector.has_table("admin_queue"):
            AdminQueue.__table__.create(self.engine)
            self._migrate_admin_queue_if_needed()

    def _migrate_admin_queue_if_needed(self):
        from sqlalchemy import delete

        admin_rows = (
            self.session.query(MessageQueue)
            .filter(MessageQueue.telegram_id == 0)
            .filter(MessageQueue.sent.is_(False))
            .all()
        )
        for row in admin_rows:
            if row.message.startswith("ADMIN_OPERATION:"):
                op_str = row.message.replace("ADMIN_OPERATION:", "").strip()
                parts = op_str.split(":")
                operation = parts[0]
                payload = ":".join(parts[1:]) if len(parts) > 1 else None
                admin_entry = AdminQueue(
                    operation=operation,
                    payload=payload,
                    created_at=row.created_at,
                    processed=False,
                )
                self.session.add(admin_entry)
        if admin_rows:
            ids_to_delete = [row.id for row in admin_rows]
            stmt = delete(MessageQueue).where(MessageQueue.id.in_(ids_to_delete))
            self.session.execute(stmt)
            self.session.commit()
            logger = logging.getLogger(__name__)
            logger.info(f"Migrated {len(admin_rows)} admin operations to admin_queue")

    @staticmethod
    def normalize_city(city: str) -> str:
        if not city:
            return ""
        return city.strip().casefold()

    def _migrate_single_city_to_multi(self):
        users = self.session.query(User).all()
        for user in users:
            normalized = self.normalize_city(user.city)
            if normalized:
                existing = (
                    self.session.query(UserCity)
                    .filter_by(user_id=user.telegram_id, city=normalized)
                    .first()
                )
                if not existing:
                    user_city = UserCity(user_id=user.telegram_id, city=normalized)
                    self.session.add(user_city)
        self.session.commit()

    def get_user_cities(self, telegram_id: int) -> list[str]:
        cities = (
            self.session.query(UserCity)
            .filter_by(user_id=telegram_id)
            .order_by(UserCity.created_at)
            .all()
        )
        return [c.city for c in cities]

    def set_user_cities(self, telegram_id: int, cities: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for city in cities:
            norm = self.normalize_city(city)
            if norm and norm not in seen:
                normalized.append(norm)
                seen.add(norm)
        normalized = normalized[:3]
        self.session.query(UserCity).filter_by(user_id=telegram_id).delete()
        for city in normalized:
            user_city = UserCity(user_id=telegram_id, city=city)
            self.session.add(user_city)
        self.session.commit()
        return normalized

    def get_city_classification(self, normalized_name: str) -> tuple[bool | None, str]:
        entry = (
            self.session.query(CityClassificationCache)
            .filter_by(normalized_name=normalized_name)
            .first()
        )
        if not entry:
            return (None, "")
        ttl_days = 730
        cutoff = self._get_utc_now() - timedelta(days=ttl_days)
        created = self._ensure_timezone_aware(entry.created_at)
        if created and created < cutoff:
            return (None, "")
        return (entry.is_city, entry.canonical_name or "")

    def set_city_classification(
        self, normalized_name: str, is_city: bool, canonical_name: str = ""
    ):
        existing = (
            self.session.query(CityClassificationCache)
            .filter_by(normalized_name=normalized_name)
            .first()
        )
        if existing:
            existing.is_city = is_city
            existing.canonical_name = canonical_name
            existing.created_at = self._get_utc_now()
        else:
            entry = CityClassificationCache(
                normalized_name=normalized_name,
                is_city=is_city,
                canonical_name=canonical_name,
                created_at=self._get_utc_now(),
            )
            self.session.add(entry)
        self.session.commit()

    def get_team_city(self, team_name: str) -> str | None:
        normalized = self.normalize_city(team_name)
        if not normalized:
            return None
        entry = self.session.query(TeamCityCache).filter_by(normalized_team_name=normalized).first()
        if not entry:
            return None
        ttl_days = 730
        cutoff = self._get_utc_now() - timedelta(days=ttl_days)
        created = self._ensure_timezone_aware(entry.created_at)
        if created and created < cutoff:
            return None
        return entry.city

    def set_team_city(self, team_name: str, city: str) -> None:
        normalized = self.normalize_city(team_name)
        if not normalized:
            return
        existing = (
            self.session.query(TeamCityCache).filter_by(normalized_team_name=normalized).first()
        )
        if existing:
            existing.city = city
            existing.created_at = self._get_utc_now()
        else:
            entry = TeamCityCache(
                normalized_team_name=normalized,
                city=city,
                created_at=self._get_utc_now(),
            )
            self.session.add(entry)
        self.session.commit()

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
        return self.mark_user_blocked(telegram_id)

    def unblock_user(self, telegram_id: int) -> bool:
        return self.mark_user_unblocked(telegram_id)

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
        return datetime.now(tz=UTC_ZONE)

    def _ensure_timezone_aware(self, dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))

    def update_last_notification(self, telegram_id: int, is_manual: bool = False):
        user = self.get_user(telegram_id)
        if user:
            now = self._get_utc_now()
            user.last_notification = now
            if is_manual:
                user.last_manual_notification = now
            self.session.commit()

    def can_send_manual_notification(self, telegram_id: int, cooldown_minutes: int = 5) -> bool:
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
        user = self.get_user(telegram_id)
        if user and user.last_notification:
            tz_aware = self._ensure_timezone_aware(user.last_notification)
            if tz_aware is None:
                return "Never"
            assert tz_aware is not None
            local_time = tz_aware.astimezone(config.TIMEZONE_INFO)
            return local_time.strftime("%Y-%m-%d %H:%M:%S")
        return "Never"

    def format_datetime(self, value: datetime | None) -> str:
        if not value:
            return "Never"
        tz_aware = self._ensure_timezone_aware(value)
        if not tz_aware:
            return "Never"
        return tz_aware.astimezone(config.TIMEZONE_INFO).strftime("%Y-%m-%d %H:%M:%S")

    def update_scheduler_last_run(self):
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE scheduler_state SET last_run = :now WHERE id = 1"),
                {"now": self._get_utc_now()},
            )

    def get_scheduler_last_run(self) -> datetime | None:
        result = self.session.query(SchedulerState).first()
        if result and result.last_run:
            tz_run = self._ensure_timezone_aware(result.last_run)
            if tz_run is not None:
                return tz_run
        return None

    def get_event_cache(
        self, city: str, target_date: date | datetime, query_type: str = "general"
    ) -> dict[str, Any] | None:
        normalized_city = self.normalize_city(city)
        if not normalized_city:
            return None

        if isinstance(target_date, datetime):
            target_date = target_date.date()

        date_key = target_date.isoformat()
        entry = (
            self.session.query(EventCache)
            .filter_by(city=normalized_city, date=date_key, query_type=query_type)
            .first()
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
        query_type: str = "general",
    ) -> None:
        normalized_city = self.normalize_city(city)
        if not normalized_city:
            return

        if isinstance(target_date, datetime):
            target_date = target_date.date()

        date_key = target_date.isoformat()
        existing = (
            self.session.query(EventCache)
            .filter_by(city=normalized_city, date=date_key, query_type=query_type)
            .first()
        )

        payload = json.dumps(events or [], ensure_ascii=False)

        if existing:
            existing.status = status
            existing.events = payload
        else:
            cache_entry = EventCache(
                city=normalized_city,
                date=date_key,
                query_type=query_type,
                status=status,
                events=payload,
            )
            self.session.add(cache_entry)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            existing = (
                self.session.query(EventCache)
                .filter_by(city=normalized_city, date=date_key, query_type=query_type)
                .first()
            )
            if existing:
                existing.status = status
                existing.events = payload
                self.session.commit()

    def queue_message(self, telegram_id: int, message: str) -> bool:
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
        return (
            self.session.query(MessageQueue)
            .filter(MessageQueue.sent.is_(False))
            .order_by(MessageQueue.created_at)
            .limit(limit)
            .all()
        )

    def mark_message_sent(self, message_id: int, sent_message_id: int | None = None) -> bool:
        try:
            message = self.session.get(MessageQueue, message_id)
            if message:
                message.sent = True
                message.sent_at = self._get_utc_now()
                if sent_message_id is not None:
                    message.sent_message_id = sent_message_id
                self.session.commit()
                return True
            return False
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error marking message as sent: {str(e)}")
            return False

    def delete_pending_messages_older_than(self, hours: int = 24) -> int:
        from sqlalchemy import delete

        cutoff = self._get_utc_now() - timedelta(hours=hours)
        stmt = (
            delete(MessageQueue)
            .where(MessageQueue.sent.is_(False))
            .where(MessageQueue.created_at < cutoff)
        )
        result = self.session.execute(stmt)
        self.session.commit()
        count = result.rowcount
        if count > 0:
            logger = logging.getLogger(__name__)
            logger.info(f"Deleted {count} pending messages older than {hours} hours")
        return count

    def delete_pending_messages_for_user_last_n_hours(
        self, telegram_id: int, hours: int = 24
    ) -> int:
        from sqlalchemy import delete

        cutoff = self._get_utc_now() - timedelta(hours=hours)
        stmt = (
            delete(MessageQueue)
            .where(MessageQueue.telegram_id == telegram_id)
            .where(MessageQueue.sent.is_(False))
            .where(MessageQueue.created_at >= cutoff)
        )
        result = self.session.execute(stmt)
        self.session.commit()
        count = result.rowcount
        if count > 0:
            logger = logging.getLogger(__name__)
            logger.info(
                f"Deleted {count} pending messages for user {telegram_id} from last {hours} hours"
            )
        return count

    def get_sent_messages_for_user_within_hours(
        self, telegram_id: int, hours: int = 1, limit: int = 500
    ) -> list[MessageQueue]:
        cutoff = self._get_utc_now() - timedelta(hours=hours)
        return (
            self.session.query(MessageQueue)
            .filter(MessageQueue.telegram_id == telegram_id)
            .filter(MessageQueue.sent.is_(True))
            .filter(MessageQueue.sent_message_id.isnot(None))
            .filter(MessageQueue.sent_at >= cutoff)
            .order_by(MessageQueue.sent_at.desc())
            .limit(limit)
            .all()
        )

    async def delete_sent_messages_for_user_within_hours(
        self, bot, telegram_id: int, hours: int = 1
    ) -> dict[str, Any]:
        messages = self.get_sent_messages_for_user_within_hours(telegram_id, hours)
        success_count = 0
        error_count = 0
        errors: list[str] = []
        logger = logging.getLogger(__name__)

        for msg in messages:
            if msg.sent_message_id is None:
                continue
            try:
                await bot.bot.delete_message(chat_id=telegram_id, message_id=msg.sent_message_id)
                success_count += 1
                logger.debug("Deleted message %s for user %s", msg.sent_message_id, telegram_id)
            except Exception as exc:
                error_count += 1
                error_text = str(exc)
                errors.append(f"Message {msg.sent_message_id}: {error_text}")
                logger.warning(
                    "Failed to delete message %s for user %s: %s",
                    msg.sent_message_id,
                    telegram_id,
                    error_text,
                )

        logger.info(
            "Deleted %s messages for user %s (errors: %s)",
            success_count,
            telegram_id,
            error_count,
        )

        return {
            "success_count": success_count,
            "error_count": error_count,
            "total_attempted": len(messages),
            "errors": errors,
        }

    def clear_city_classification_cache(self) -> int:
        from sqlalchemy import delete

        stmt = delete(CityClassificationCache)
        result = self.session.execute(stmt)
        self.session.commit()
        count = result.rowcount
        logger = logging.getLogger(__name__)
        logger.info(f"Cleared {count} entries from city classification cache")
        return count

    def close(self) -> None:
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

    def get_blocked_users(self) -> list[User]:
        return self.session.query(User).filter_by(is_blocked=True).all()

    def mark_user_blocked(self, telegram_id: int, timestamp: datetime | None = None) -> bool:
        user = self.get_user(telegram_id)
        if not user:
            return False
        now = timestamp or self._get_utc_now()
        user.is_blocked = True
        user.blocked_at = now
        user.last_block_status_check_at = now
        self.session.commit()
        return True

    def mark_user_unblocked(self, telegram_id: int, timestamp: datetime | None = None) -> bool:
        user = self.get_user(telegram_id)
        if not user:
            return False
        now = timestamp or self._get_utc_now()
        user.is_blocked = False
        user.blocked_at = None
        user.last_block_status_check_at = now
        self.session.commit()
        return True

    async def recheck_blocked_users(self, bot) -> dict[str, Any]:
        blocked_users = self.get_blocked_users()
        checked = len(blocked_users)
        unblocked = 0
        still_blocked = 0
        errors: list[str] = []
        logger = logging.getLogger(__name__)

        for user in blocked_users:
            user_id = user.telegram_id
            logger.debug("Rechecking blocked user %s", user_id)
            check_time = self._get_utc_now()
            try:
                message = await bot.bot.send_message(
                    chat_id=user_id, text="test-message", disable_notification=True
                )
                await bot.bot.delete_message(chat_id=user_id, message_id=message.message_id)
                self.mark_user_unblocked(user_id, timestamp=check_time)
                unblocked += 1
            except Exception as exc:
                error_text = str(exc)
                if is_user_blocked_error(error_text):
                    self.mark_user_blocked(user_id, timestamp=check_time)
                    still_blocked += 1
                else:
                    user.last_block_status_check_at = check_time
                    self.session.commit()
                    errors.append(f"User {user_id}: {error_text}")

        logger.info(
            "Blocked recheck: %s checked, %s unblocked, %s still blocked",
            checked,
            unblocked,
            still_blocked,
        )

        return {
            "checked": checked,
            "unblocked": unblocked,
            "still_blocked": still_blocked,
            "errors": errors,
        }

    def record_exa_cost(self, source: str, cost: float) -> None:
        cost_microdollars = int(cost * 1_000_000)
        entry = ExaCost(source=source, cost=cost_microdollars, created_at=self._get_utc_now())
        self.session.add(entry)
        self.session.commit()

    def get_total_exa_cost(self) -> float:
        from sqlalchemy import func

        result = self.session.query(func.sum(ExaCost.cost)).scalar()
        if result is None:
            return 0.0
        return result / 1_000_000

    def get_exa_cost_by_source(self) -> dict[str, float]:
        from sqlalchemy import func

        results = (
            self.session.query(ExaCost.source, func.sum(ExaCost.cost))
            .group_by(ExaCost.source)
            .all()
        )
        return {source: total / 1_000_000 for source, total in results}

    def upsert_pending_request(self, telegram_id: int, username: str | None) -> None:
        existing = (
            self.session.query(PendingAccessRequest).filter_by(telegram_id=telegram_id).first()
        )
        now = self._get_utc_now()
        if existing:
            existing.username = username
            existing.last_seen = now
        else:
            request = PendingAccessRequest(
                telegram_id=telegram_id, username=username, first_seen=now, last_seen=now
            )
            self.session.add(request)
        self.session.commit()

    def remove_pending_request(self, telegram_id: int) -> bool:
        result = (
            self.session.query(PendingAccessRequest).filter_by(telegram_id=telegram_id).delete()
        )
        self.session.commit()
        return result > 0

    def list_pending_requests(self) -> list[PendingAccessRequest]:
        return (
            self.session.query(PendingAccessRequest).order_by(PendingAccessRequest.first_seen).all()
        )

    def delete_event_cache(self, city: str, target_date: date | datetime) -> int:
        from sqlalchemy import delete

        normalized_city = self.normalize_city(city)
        if not normalized_city:
            return 0

        if isinstance(target_date, datetime):
            target_date = target_date.date()

        date_key = target_date.isoformat()
        stmt = delete(EventCache).where(
            EventCache.city == normalized_city, EventCache.date == date_key
        )
        result = self.session.execute(stmt)
        self.session.commit()
        return result.rowcount

    def get_all_cities_with_users(self) -> list[str]:
        cities = self.session.query(UserCity.city).distinct().all()
        return [c[0] for c in cities]

    def should_send_denial(self, telegram_id: int, cooldown_seconds: int = 300) -> bool:
        now = self._get_utc_now()
        entry = self.session.query(AccessDenialLog).filter_by(telegram_id=telegram_id).first()
        if entry:
            last_sent = self._ensure_timezone_aware(entry.last_sent)
            if last_sent is None:
                return True
            time_since_last = now - last_sent
            if time_since_last.total_seconds() < cooldown_seconds:
                return False
            entry.last_sent = now
        else:
            new_entry = AccessDenialLog(telegram_id=telegram_id, last_sent=now)
            self.session.add(new_entry)
        self.session.commit()
        return True

    def enqueue_admin_operation(
        self, operation: str, params: list[str] | tuple[str, ...] | None = None
    ) -> bool:
        try:
            payload = ":".join(params) if params else None
            queue_item = AdminQueue(
                operation=operation,
                payload=payload,
                created_at=self._get_utc_now(),
                processed=False,
            )
            self.session.add(queue_item)
            self.session.commit()
            logger = logging.getLogger(__name__)
            logger.info(f"Admin operation queued: {operation}")
            return True
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error queueing admin operation: {str(e)}")
            return False

    def get_pending_admin_operations(self, limit: int = 10) -> list[AdminQueue]:
        return (
            self.session.query(AdminQueue)
            .filter(AdminQueue.processed.is_(False))
            .order_by(AdminQueue.created_at)
            .limit(limit)
            .all()
        )

    def mark_admin_operation_processed(self, operation_id: int) -> bool:
        try:
            operation = self.session.get(AdminQueue, operation_id)
            if operation:
                operation.processed = True
                operation.processed_at = self._get_utc_now()
                self.session.commit()
                return True
            return False
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error marking admin operation as processed: {str(e)}")
            return False
