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

    def check_access(self, telegram_id: int) -> bool:  # pragma: no cover - stub
        return self.allowed


class StubBotDB:
    def __init__(self, user=None):
        self.user = user
        self.added: list[tuple[int, str, str]] = []

    def get_user(self, telegram_id: int):
        return self.user

    def add_user(self, telegram_id: int, username: str, city: str):
        self.added.append((telegram_id, username, city))

    async def check_access(self, telegram_id: int):
        return True


@pytest.mark.anyio
async def test_check_access_delegates_to_db(monkeypatch):
    monkeypatch.setattr(bot, "db", DummyDB(True))
    update = DummyUpdate(123)
    assert await bot.check_access(update)


@pytest.mark.anyio
async def test_handle_unauthorized_replies_and_logs(monkeypatch):
    monkeypatch.setattr(bot, "db", DummyDB(False))
    update = DummyUpdate(321)
    await bot.handle_unauthorized(update)
    assert update.message.sent
    assert bot.MSG_UNAUTHORIZED in update.message.sent[0][0]


@pytest.mark.anyio
async def test_handle_invalid_input_returns_end(monkeypatch):
    update = DummyUpdate(1)
    result = await bot.handle_invalid_input(update, {})
    assert result == bot.ConversationHandler.END


@pytest.mark.anyio
async def test_start_new_user_shows_welcome(monkeypatch):
    async def allow(_: object) -> bool:
        return True

    monkeypatch.setattr(bot, "check_access", allow)
    stub_db = StubBotDB()
    monkeypatch.setattr(bot, "db", stub_db)

    update = DummyUpdate(42)
    update.effective_user.username = "tester"

    await bot.start(update, {})
    assert bot.MSG_WELCOME_NEW in update.message.sent[0][0]


@pytest.mark.anyio
async def test_start_existing_user_displays_city(monkeypatch):
    async def allow(_: object) -> bool:
        return True

    monkeypatch.setattr(bot, "check_access", allow)
    stub_db = StubBotDB(user=type("U", (), {"city": "Roma"}))
    monkeypatch.setattr(bot, "db", stub_db)

    update = DummyUpdate(99)
    update.effective_user.username = "returning"

    await bot.start(update, {})
    assert "Bentornato" in update.message.sent[0][0]


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

    result = await bot.set_city(update, {})
    assert result == bot.ConversationHandler.END
    assert stub_db.added[-1] == (5, "tester", "Torino")


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
