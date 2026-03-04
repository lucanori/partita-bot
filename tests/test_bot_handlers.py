import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import partita_bot.bot as bot


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[tuple[str, object | None]] = []

    async def reply_text(self, text: str, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeUser:
    def __init__(self, user_id: int, username: str = "tester"):
        self.id = user_id
        self.username = username


class FakeUpdate:
    def __init__(self, user_id: int = 1, text: str = ""):
        self.effective_user = FakeUser(user_id)
        self.message = FakeMessage(text)
        self.effective_message = self.message


class FakeUserObj:
    def __init__(
        self, telegram_id: int, username: str = "tester", city: str = "", last_notification=None
    ):
        self.telegram_id = telegram_id
        self.username = username
        self.city = city
        self.last_notification = last_notification
        self.is_blocked = False


class FakeDB:
    def __init__(
        self,
        access=True,
        existing_user=None,
        user_cities=None,
        mode="blocklist",
        denial_cooldown_responses=None,
    ):
        self.access = access
        self._user = existing_user
        self.user_cities = user_cities or []
        self.added: list[tuple[int, str, str]] = []
        self.cities_set: list[list[str]] = []
        self.mode = mode
        self.pending_upserted: list[tuple[int, str | None]] = []
        self.denial_cooldown_responses = denial_cooldown_responses or {}
        self.denial_calls: list[tuple[int, int]] = []
        self.queued_messages: list[tuple[int, str]] = []
        self.last_notification_updated: list[int] = []

    def check_access(self, telegram_id: int) -> bool:
        return self.access

    def get_access_mode(self) -> str:
        return self.mode

    def upsert_pending_request(self, telegram_id: int, username: str | None):
        self.pending_upserted.append((telegram_id, username))

    def should_send_denial(self, telegram_id: int, cooldown_seconds: int = 300) -> bool:
        self.denial_calls.append((telegram_id, cooldown_seconds))
        return self.denial_cooldown_responses.get(telegram_id, True)

    def get_user(self, telegram_id: int):
        return self._user

    def add_user(self, telegram_id: int, username: str, city: str):
        self.added.append((telegram_id, username, city))
        if self._user is None:
            self._user = FakeUserObj(
                telegram_id=telegram_id,
                username=username,
                city=city,
                last_notification=None,
            )
        else:
            self._user.username = username
            self._user.city = city
        return self._user

    def get_user_cities(self, telegram_id: int) -> list[str]:
        return self.user_cities

    def set_user_cities(self, telegram_id: int, cities: list[str]) -> list[str]:
        self.cities_set.append(cities)
        self.user_cities = cities
        return cities

    def get_city_classification(self, normalized_name: str):
        return (None, "")

    def set_city_classification(
        self, normalized_name: str, is_city: bool, canonical_name: str = ""
    ):
        pass

    def queue_message(self, telegram_id: int, message: str) -> bool:
        self.queued_messages.append((telegram_id, message))
        return True

    def update_last_notification(self, telegram_id: int, is_manual: bool = False):
        self.last_notification_updated.append(telegram_id)
        if self._user:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            self._user.last_notification = datetime.now(tz=ZoneInfo("UTC"))

    @staticmethod
    def normalize_city(city: str) -> str:
        return city.strip().casefold()


def _make_update(user_id: int = 42, text: str = "") -> FakeUpdate:
    return FakeUpdate(user_id=user_id, text=text)


def test_start_unauthorized_blocklist(monkeypatch):
    fake_db = FakeDB(access=False, mode="blocklist")
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update()
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert update.message.replies
    assert update.message.replies[0][0] == bot.MSG_UNAUTHORIZED


def test_start_unauthorized_whitelist(monkeypatch):
    fake_db = FakeDB(access=False, mode="whitelist")
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(user_id=12345)
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert update.message.replies
    expected_msg = bot.MSG_UNAUTHORIZED_WHITELIST.format(user_id=12345)
    assert update.message.replies[0][0] == expected_msg
    assert fake_db.pending_upserted == [(12345, "tester")]


def test_start_new_user_shows_welcome(monkeypatch):
    fake_db = FakeDB(access=True, existing_user=None)
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(user_id=10)
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert update.message.replies[0][0] == bot.MSG_WELCOME_NEW


def test_start_existing_user_shows_current_city(monkeypatch):
    fake_db = FakeDB(
        access=True, existing_user=FakeUserObj(21, city="Verona"), user_cities=["verona"]
    )
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(user_id=21)
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert "Bentornato" in update.message.replies[0][0]


class FakeEventFetcher:
    def __init__(self, db):
        self.db = db

    def classify_city(self, location: str):
        return (True, location.strip().casefold())


def test_set_city_records_choice(monkeypatch, freezer):
    freezer.move_to(datetime(2026, 3, 4, 11, 0, tzinfo=ZoneInfo("Europe/Rome")))
    fake_db = FakeDB(access=True)
    monkeypatch.setattr(bot, "db", fake_db)
    monkeypatch.setattr(bot, "EventFetcher", FakeEventFetcher)
    update = _make_update(text="  roma  ")
    asyncio.run(bot.set_city(update, SimpleNamespace()))
    assert fake_db.added
    assert fake_db.added[0][2] == "roma"
    assert fake_db.cities_set
    assert "roma" in fake_db.cities_set[0]
    assert "Ho impostato le tue città" in update.message.replies[0][0]


def test_error_handler_replies(monkeypatch):
    fake_db = FakeDB(access=True)
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update()
    context = SimpleNamespace(error=RuntimeError("boom"))
    asyncio.run(bot.error_handler(update, context))
    assert update.message.replies
    assert "Si è verificato un errore" in update.message.replies[0][0]


def test_start_unauthorized_blocklist_cooldown_suppresses(monkeypatch):
    fake_db = FakeDB(access=False, mode="blocklist", denial_cooldown_responses={42: False})
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(user_id=42)
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert not update.message.replies
    assert fake_db.denial_calls == [(42, 300)]


def test_start_unauthorized_whitelist_cooldown_suppresses_but_upserts(monkeypatch):
    fake_db = FakeDB(access=False, mode="whitelist", denial_cooldown_responses={12345: False})
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(user_id=12345)
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert not update.message.replies
    assert fake_db.pending_upserted == [(12345, "tester")]
    assert fake_db.denial_calls == [(12345, 300)]


def test_start_unauthorized_cooldown_allows_when_true(monkeypatch):
    fake_db = FakeDB(access=False, mode="blocklist", denial_cooldown_responses={99: True})
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(user_id=99)
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert update.message.replies
    assert update.message.replies[0][0] == bot.MSG_UNAUTHORIZED
    assert fake_db.denial_calls == [(99, 300)]


class FakeEventFetcherWithEvents:
    def __init__(self, db):
        self.db = db

    def classify_city(self, location: str):
        return (True, location.strip().casefold())

    def fetch_event_message(self, city: str, target_date):
        return f"Eventi per {city}"


def test_set_city_inside_window_sends_notification(monkeypatch, freezer):
    freezer.move_to(datetime(2026, 3, 4, 8, 30, tzinfo=ZoneInfo("Europe/Rome")))

    fake_user = FakeUserObj(
        telegram_id=42,
        last_notification=None,
    )
    fake_db = FakeDB(access=True, existing_user=fake_user, user_cities=["roma"])
    monkeypatch.setattr(bot, "db", fake_db)
    monkeypatch.setattr(bot, "EventFetcher", FakeEventFetcherWithEvents)
    monkeypatch.setattr(bot.config, "TIMEZONE_INFO", ZoneInfo("Europe/Rome"))

    update = _make_update(user_id=42, text="roma")
    asyncio.run(bot.set_city(update, SimpleNamespace()))

    assert len(fake_db.queued_messages) == 1
    assert fake_db.queued_messages[0][0] == 42
    assert fake_db.last_notification_updated == [42]


def test_set_city_outside_window_skips_notification(monkeypatch, freezer):
    freezer.move_to(datetime(2026, 3, 4, 11, 0, tzinfo=ZoneInfo("Europe/Rome")))

    fake_user = FakeUserObj(
        telegram_id=42,
        last_notification=None,
    )
    fake_db = FakeDB(access=True, existing_user=fake_user, user_cities=["roma"])
    monkeypatch.setattr(bot, "db", fake_db)
    monkeypatch.setattr(bot, "EventFetcher", FakeEventFetcherWithEvents)
    monkeypatch.setattr(bot.config, "TIMEZONE_INFO", ZoneInfo("Europe/Rome"))

    update = _make_update(user_id=42, text="roma")
    asyncio.run(bot.set_city(update, SimpleNamespace()))

    assert len(fake_db.queued_messages) == 0
    assert fake_db.last_notification_updated == []


def test_set_city_before_window_skips_notification(monkeypatch, freezer):
    freezer.move_to(datetime(2026, 3, 4, 6, 0, tzinfo=ZoneInfo("Europe/Rome")))

    fake_user = FakeUserObj(
        telegram_id=42,
        last_notification=None,
    )
    fake_db = FakeDB(access=True, existing_user=fake_user, user_cities=["roma"])
    monkeypatch.setattr(bot, "db", fake_db)
    monkeypatch.setattr(bot, "EventFetcher", FakeEventFetcherWithEvents)
    monkeypatch.setattr(bot.config, "TIMEZONE_INFO", ZoneInfo("Europe/Rome"))

    update = _make_update(user_id=42, text="roma")
    asyncio.run(bot.set_city(update, SimpleNamespace()))

    assert len(fake_db.queued_messages) == 0
    assert fake_db.last_notification_updated == []


def test_set_city_already_notified_today_skips(monkeypatch, freezer):
    freezer.move_to(datetime(2026, 3, 4, 8, 30, tzinfo=ZoneInfo("Europe/Rome")))

    fake_user = FakeUserObj(
        telegram_id=42,
        last_notification=datetime(2026, 3, 4, 7, 0, tzinfo=ZoneInfo("UTC")),
    )
    fake_db = FakeDB(access=True, existing_user=fake_user, user_cities=["roma"])
    monkeypatch.setattr(bot, "db", fake_db)
    monkeypatch.setattr(bot, "EventFetcher", FakeEventFetcherWithEvents)
    monkeypatch.setattr(bot.config, "TIMEZONE_INFO", ZoneInfo("Europe/Rome"))

    update = _make_update(user_id=42, text="roma")
    asyncio.run(bot.set_city(update, SimpleNamespace()))

    assert len(fake_db.queued_messages) == 0
    assert fake_db.last_notification_updated == []


class FakeEventFetcherNoEvents:
    def __init__(self, db):
        self.db = db

    def classify_city(self, location: str):
        return (True, location.strip().casefold())

    def fetch_event_message(self, city: str, target_date):
        return ""


def test_set_city_no_events_does_nothing(monkeypatch, freezer):
    freezer.move_to(datetime(2026, 3, 4, 8, 30, tzinfo=ZoneInfo("Europe/Rome")))

    fake_user = FakeUserObj(
        telegram_id=42,
        last_notification=None,
    )
    fake_db = FakeDB(access=True, existing_user=fake_user, user_cities=["roma"])
    monkeypatch.setattr(bot, "db", fake_db)
    monkeypatch.setattr(bot, "EventFetcher", FakeEventFetcherNoEvents)
    monkeypatch.setattr(bot.config, "TIMEZONE_INFO", ZoneInfo("Europe/Rome"))

    update = _make_update(user_id=42, text="roma")
    asyncio.run(bot.set_city(update, SimpleNamespace()))

    assert len(fake_db.queued_messages) == 0
    assert fake_db.last_notification_updated == []
