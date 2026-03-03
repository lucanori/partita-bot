import asyncio
from types import SimpleNamespace

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


class FakeDB:
    def __init__(self, access=True, existing_user=None):
        self.access = access
        self.user = existing_user
        self.added: list[tuple[int, str, str]] = []

    def check_access(self, telegram_id: int) -> bool:
        return self.access

    def get_user(self, telegram_id: int):
        return self.user

    def add_user(self, telegram_id: int, username: str, city: str):
        self.added.append((telegram_id, username, city))
        return SimpleNamespace(telegram_id=telegram_id, username=username, city=city)


def _make_update(user_id: int = 42, text: str = "") -> FakeUpdate:
    return FakeUpdate(user_id=user_id, text=text)


def test_start_unauthorized(monkeypatch):
    fake_db = FakeDB(access=False)
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update()
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert update.message.replies
    assert update.message.replies[0][0] == bot.MSG_UNAUTHORIZED


def test_start_new_user_shows_welcome(monkeypatch):
    fake_db = FakeDB(access=True, existing_user=None)
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(user_id=10)
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert update.message.replies[0][0] == bot.MSG_WELCOME_NEW


def test_start_existing_user_shows_current_city(monkeypatch):
    fake_db = FakeDB(access=True, existing_user=SimpleNamespace(city="Verona"))
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(user_id=21)
    asyncio.run(bot.start(update, SimpleNamespace()))
    assert "Bentornato" in update.message.replies[0][0]


def test_set_city_records_choice(monkeypatch):
    fake_db = FakeDB(access=True)
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update(text="  roma  ")
    asyncio.run(bot.set_city(update, SimpleNamespace()))
    assert fake_db.added
    assert fake_db.added[0][2] == "roma"
    assert "Ho impostato la tua città" in update.message.replies[0][0]


def test_error_handler_replies(monkeypatch):
    fake_db = FakeDB(access=True)
    monkeypatch.setattr(bot, "db", fake_db)
    update = _make_update()
    context = SimpleNamespace(error=RuntimeError("boom"))
    asyncio.run(bot.error_handler(update, context))
    assert update.message.replies
    assert "Si è verificato un errore" in update.message.replies[0][0]
