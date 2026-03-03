import pytest

import partita_bot.bot as bot


class DummyMessage:
    def __init__(self):
        self.sent: list[tuple[str, object]] = []
        self.text = "payload"

    async def reply_text(self, text: str, reply_markup=None):
        self.sent.append((text, reply_markup))


class DummyUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username: str | None = None


class DummyUpdate:
    def __init__(self, user_id: int):
        self.effective_user = DummyUser(user_id)
        self.message = DummyMessage()
        self.effective_message = self.message


class DummyDB:
    def __init__(self, allowed: bool):
        self.allowed = allowed

    def check_access(self, telegram_id: int) -> bool:
        return self.allowed


class StubBotDB:
    def __init__(self, user=None, user_cities=None):
        self.user = user
        self.user_cities = user_cities or []
        self.added: list[tuple[int, str, str]] = []
        self.cities_set: list[list[str]] = []

    def get_user(self, telegram_id: int):
        return self.user

    def add_user(self, telegram_id: int, username: str, city: str):
        self.added.append((telegram_id, username, city))

    async def check_access(self, telegram_id: int):
        return True

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

    @staticmethod
    def normalize_city(city: str) -> str:
        return city.strip().casefold()


class FakeEventFetcher:
    def __init__(self, db):
        self.db = db

    def classify_city(self, location: str):
        return (True, location.strip().casefold())


@pytest.mark.anyio
async def test_city_conversation_flows(monkeypatch):
    async def allow(_: object) -> bool:
        return True

    monkeypatch.setattr(bot, "check_access", allow)

    update = DummyUpdate(5)
    result = await bot.start_city_input(update, {})
    assert result == bot.WAITING_FOR_CITY
    assert bot.MSG_CITY_PROMPT in update.message.sent[0][0]

    update.message.sent.clear()
    update.message.text = "Torino"
    update.effective_user.username = "tester"
    stub_db = StubBotDB()
    monkeypatch.setattr(bot, "db", stub_db)
    monkeypatch.setattr(bot, "EventFetcher", FakeEventFetcher)

    result = await bot.set_city(update, {})
    assert result == bot.ConversationHandler.END
    assert stub_db.added[-1] == (5, "tester", "Torino")
    assert stub_db.cities_set
    assert "torino" in stub_db.cities_set[0]


@pytest.mark.anyio
async def test_show_keyboard_and_error_handler(monkeypatch):
    async def allow(_: object) -> bool:
        return True

    monkeypatch.setattr(bot, "check_access", allow)
    update = DummyUpdate(7)
    await bot.show_keyboard(update, {})
    assert "Usa il pulsante" in update.message.sent[-1][0]

    context = type("Ctx", (), {"error": RuntimeError("boom")})()
    await bot.error_handler(update, context)
    assert "Si è verificato un errore" in update.message.sent[-1][0]
