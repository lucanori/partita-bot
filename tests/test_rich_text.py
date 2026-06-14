import json
from base64 import b64encode
from datetime import date

import pytest
from telegram import LinkPreviewOptions, MessageEntity

import partita_bot.config as config
from partita_bot.event_fetcher import EventFetcher
from partita_bot.rich_text import (
    RichMessage,
    RichMessageBuilder,
    RichMessageStorage,
    deserialize_entities,
    deserialize_link_preview_options,
    rich_message_from_queue_row,
)
from partita_bot.storage import Database


def _auth_header() -> dict[str, str]:
    token = b64encode(
        f"{config.ADMIN_USERNAME}:{config.ADMIN_PASSWORD}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


class TestRichMessageFromJson:
    def test_valid_json_with_entities(self):
        payload = json.dumps({
            "text": "Hello **bold** world",
            "entities": [
                {"type": "bold", "offset": 6, "length": 6},
                {
                    "type": "text_link",
                    "offset": 14,
                    "length": 5,
                    "url": "https://example.com",
                },
            ],
        })
        msg = RichMessage.from_json(payload)
        assert msg.text == "Hello **bold** world"
        assert msg.parse_mode is None
        assert msg.entities is not None
        assert len(msg.entities) == 2
        assert msg.entities[0].type == "bold"
        assert msg.entities[0].offset == 6
        assert msg.entities[0].length == 6
        assert msg.entities[1].type == "text_link"
        assert msg.entities[1].url == "https://example.com"

    def test_valid_json_with_parse_mode_only(self):
        payload = json.dumps({
            "text": "Some *markdown* text",
            "parse_mode": "Markdown",
        })
        msg = RichMessage.from_json(payload)
        assert msg.text == "Some *markdown* text"
        assert msg.parse_mode == "Markdown"
        assert msg.entities is None

    def test_valid_json_with_link_preview_options(self):
        payload = json.dumps({
            "text": "Check this out",
            "link_preview_options": {"is_disabled": True},
        })
        msg = RichMessage.from_json(payload)
        assert msg.text == "Check this out"
        assert msg.link_preview_options is not None
        assert msg.link_preview_options.is_disabled is True

    def test_entities_win_over_parse_mode(self):
        payload = json.dumps({
            "text": "Bold and italic",
            "parse_mode": "HTML",
            "entities": [
                {"type": "bold", "offset": 0, "length": 4},
            ],
        })
        msg = RichMessage.from_json(payload)
        assert msg.text == "Bold and italic"
        assert msg.parse_mode is None
        assert msg.entities is not None
        assert len(msg.entities) == 1
        assert msg.entities[0].type == "bold"

    def test_from_dict_directly(self):
        data = {"text": "hello"}
        msg = RichMessage.from_json(data)
        assert msg.text == "hello"
        assert msg.parse_mode is None
        assert msg.entities is None
        assert msg.link_preview_options is None

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="RichMessage JSON payload must be valid JSON"):
            RichMessage.from_json("not valid json {")

    def test_non_dict_payload_raises(self):
        with pytest.raises(ValueError, match="RichMessage JSON payload must be a JSON object"):
            RichMessage.from_json("[]")

    def test_missing_text_field_raises(self):
        with pytest.raises(ValueError, match="requires a 'text' field"):
            RichMessage.from_json("{}")

    def test_empty_text_raises(self):
        with pytest.raises(ValueError, match="requires a 'text' field"):
            RichMessage.from_json('{"text": ""}')

    def test_non_list_entities_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            RichMessage.from_json('{"text": "x", "entities": "bad"}')

    def test_entity_item_not_dict_raises(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            RichMessage.from_json('{"text": "x", "entities": ["bad"]}')

    def test_entity_missing_type_raises(self):
        with pytest.raises(ValueError, match="must have a 'type' field"):
            RichMessage.from_json(
                '{"text": "x", "entities": [{"offset": 0, "length": 1}]}'
            )

    def test_non_dict_link_preview_options_raises(self):
        with pytest.raises(ValueError, match="link_preview_options.*JSON object"):
            RichMessage.from_json('{"text": "x", "link_preview_options": "bad"}')


class TestRichMessageBuilderUtf16Offsets:
    def test_bold_after_emoji_prefix(self):
        builder = RichMessageBuilder()
        builder.add("\U0001f3af ")
        builder.add_bold("hello")
        msg = builder.build()
        assert msg.text == "\U0001f3af hello"
        assert msg.entities is not None
        assert len(msg.entities) == 1
        assert msg.entities[0].type == "bold"
        assert msg.entities[0].offset == 3
        assert msg.entities[0].length == 5

    def test_link_after_multiple_emojis(self):
        builder = RichMessageBuilder()
        builder.add("\U0001f4e3 \U0001f552 ")
        builder.add_link("click here", "https://example.com")
        msg = builder.build()
        assert msg.entities is not None
        assert msg.entities[0].offset == 6
        assert msg.entities[0].length == 10
        assert msg.entities[0].url == "https://example.com"

    def test_emoji_in_middle_of_text_following_bold(self):
        builder = RichMessageBuilder()
        builder.add_bold("\U0001f3af header")
        builder.add("\n")
        builder.add("\U0001f4cd ")
        builder.add_bold("location \U0001f3df\ufe0f")
        msg = builder.build()
        entities = msg.entities
        assert entities is not None
        assert len(entities) == 2
        assert entities[0].type == "bold"
        assert entities[0].offset == 0
        assert entities[0].length == 9
        assert entities[1].type == "bold"
        assert entities[1].length == 12

    def test_blockquote_after_emoji_prefix(self):
        builder = RichMessageBuilder()
        builder.add("\U0001f4e3 ")
        builder.add_blockquote("some details here")
        msg = builder.build()
        assert msg.entities is not None
        assert msg.entities[0].type == "blockquote"
        assert msg.entities[0].offset == 3
        assert msg.entities[0].length == 17

    def test_surrogate_pair_emoji_offset(self):
        builder = RichMessageBuilder()
        builder.add("\U0001f3b6")
        builder.add_bold("bold")
        msg = builder.build()
        assert msg.entities is not None
        assert msg.entities[0].offset == 2
        assert msg.entities[0].length == 4


class TestQueueRichMessagePersistence:
    @pytest.fixture
    def db(self):
        database = Database(database_url="sqlite:///:memory:")
        try:
            yield database
        finally:
            database.close()

    def test_entities_overrides_parse_mode_in_db(self, db):
        payload = json.dumps({
            "text": "Hello *markdown* world",
            "parse_mode": "Markdown",
            "entities": [
                {"type": "bold", "offset": 6, "length": 10},
            ],
            "link_preview_options": {"is_disabled": True},
        })
        msg = RichMessage.from_json(payload)
        assert db.queue_rich_message(1, msg)

        rows = db.get_pending_messages(limit=5)
        assert len(rows) == 1
        row = rows[0]
        assert row.message == "Hello *markdown* world"
        assert row.parse_mode is None
        assert row.entities_json is not None
        assert row.link_preview_options_json is not None

        entities_data = json.loads(row.entities_json)
        assert len(entities_data) == 1
        assert entities_data[0]["type"] == "bold"
        assert entities_data[0]["offset"] == 6
        assert entities_data[0]["length"] == 10

        lpo_data = json.loads(row.link_preview_options_json)
        assert lpo_data.get("is_disabled") is True

    def test_parse_mode_only_stored_in_db(self, db):
        payload = json.dumps({
            "text": "Some *markdown* text",
            "parse_mode": "Markdown",
        })
        msg = RichMessage.from_json(payload)
        assert db.queue_rich_message(2, msg)

        rows = db.get_pending_messages(limit=5)
        assert len(rows) == 1
        row = rows[0]
        assert row.parse_mode == "Markdown"
        assert row.entities_json is None
        assert row.link_preview_options_json is None

    def test_plain_text_fallback(self, db):
        plain = RichMessage.from_plain("just some text")
        assert db.queue_rich_message(2, plain)

        rows = db.get_pending_messages(limit=5)
        assert len(rows) == 1
        row = rows[0]
        assert row.message == "just some text"
        assert row.parse_mode is None
        assert row.entities_json is None
        assert row.link_preview_options_json is None

    def test_string_fallback(self, db):
        assert db.queue_rich_message(3, "plain string")

        rows = db.get_pending_messages(limit=5)
        assert len(rows) == 1
        assert rows[0].message == "plain string"

    def test_entities_null_in_db_when_no_entities(self, db):
        msg = RichMessage(text="no markup here")
        assert db.queue_rich_message(4, msg)

        rows = db.get_pending_messages(limit=5)
        assert rows[0].entities_json is None

    def test_entities_json_roundtrip_via_queue_row(self, db):
        original = RichMessage(
            text="Bold text and link",
            entities=[
                MessageEntity("bold", 0, 9),
                MessageEntity("text_link", 14, 4, url="https://x.com"),
            ],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        assert db.queue_rich_message(5, original)

        rows = db.get_pending_messages(limit=5)
        row = rows[0]
        restored = rich_message_from_queue_row(row)

        assert restored.text == "Bold text and link"
        assert restored.parse_mode is None
        assert restored.entities is not None
        assert len(restored.entities) == 2
        assert restored.entities[0].type == "bold"
        assert restored.entities[0].offset == 0
        assert restored.entities[0].length == 9
        assert restored.entities[1].type == "text_link"
        assert restored.entities[1].offset == 14
        assert restored.entities[1].length == 4
        assert restored.entities[1].url == "https://x.com"
        assert restored.link_preview_options is not None
        assert restored.link_preview_options.is_disabled is True

    def test_entities_roundtrip_with_language_and_custom_emoji(self, db):
        original = RichMessage(
            text="code and emoji",
            entities=[
                MessageEntity("code", 0, 4),
                MessageEntity("pre", 5, 3, language="python"),
                MessageEntity("custom_emoji", 9, 7, custom_emoji_id="12345"),
            ],
        )
        assert db.queue_rich_message(6, original)

        rows = db.get_pending_messages(limit=5)
        restored = rich_message_from_queue_row(rows[0])

        assert restored.entities is not None
        assert len(restored.entities) == 3
        assert restored.entities[1].language == "python"
        assert restored.entities[2].custom_emoji_id == "12345"


class TestDeserializationHelpers:
    def test_deserialize_entities_none(self):
        assert deserialize_entities(None) is None
        assert deserialize_entities("") is None

    def test_deserialize_entities_empty_list(self):
        assert deserialize_entities("[]") is None

    def test_deserialize_link_preview_none(self):
        assert deserialize_link_preview_options(None) is None
        assert deserialize_link_preview_options("") is None

    def test_deserialize_link_preview_empty_object(self):
        assert deserialize_link_preview_options("{}") is None


class TestEventFormattingShape:
    def _make_fetcher(self, db):
        return EventFetcher(db)

    @pytest.fixture
    def db(self):
        database = Database(database_url="sqlite:///:memory:")
        try:
            yield database
        finally:
            database.close()

    def test_details_use_blockquote_entity(self, db):
        fetcher = self._make_fetcher(db)
        events = [
            {
                "title": "Concerto Rock",
                "time": "21:00",
                "location": "Piazza Grande",
                "type": "Concerto",
                "details": "Band famosa in tour europeo",
                "event_date": "2026-06-15",
                "source_url": "https://example.com/event",
            }
        ]
        msg = fetcher._format_event_message("Modena", date(2026, 6, 15), events)
        assert msg.entities is not None
        blockquote_entities = [
            e for e in msg.entities if e.type == "blockquote"
        ]
        assert len(blockquote_entities) == 1
        blockquote = blockquote_entities[0]
        extracted = msg.text[
            blockquote.offset : blockquote.offset + blockquote.length
        ]
        assert "Band famosa in tour europeo" in extracted

    def test_link_preview_is_disabled(self, db):
        fetcher = self._make_fetcher(db)
        events = [
            {
                "title": "Festival",
                "time": "18:00",
                "location": "Parco",
                "type": "Festival",
                "details": "Ingresso gratuito",
                "event_date": "2026-06-15",
                "source_url": "https://example.com/festival",
            }
        ]
        msg = fetcher._format_event_message("Modena", date(2026, 6, 15), events)
        assert msg.link_preview_options is not None
        assert msg.link_preview_options.is_disabled is True

    def test_event_without_details_has_no_blockquote(self, db):
        fetcher = self._make_fetcher(db)
        events = [
            {
                "title": "Mostra",
                "time": "10:00",
                "location": "Museo",
                "type": "Mostra",
                "event_date": "2026-06-15",
                "source_url": "https://example.com/mostra",
            }
        ]
        msg = fetcher._format_event_message("Modena", date(2026, 6, 15), events)
        assert msg.entities is not None
        blockquote_entities = [
            e for e in msg.entities if e.type == "blockquote"
        ]
        assert len(blockquote_entities) == 0

    def test_source_url_becomes_text_link(self, db):
        fetcher = self._make_fetcher(db)
        events = [
            {
                "title": "Evento online",
                "time": "20:00",
                "location": "Online",
                "type": "Webinar",
                "details": "Registrazione richiesta",
                "event_date": "2026-06-15",
                "source_url": "https://example.com/webinar",
            }
        ]
        msg = fetcher._format_event_message("Modena", date(2026, 6, 15), events)
        text_link_entities = [
            e for e in msg.entities if e.type == "text_link"
        ]
        assert len(text_link_entities) >= 1
        link = text_link_entities[0]
        assert link.url == "https://example.com/webinar"

    def test_event_without_source_url_has_no_text_links(self, db):
        fetcher = self._make_fetcher(db)
        events = [
            {
                "title": "Conferenza",
                "time": "14:00",
                "location": "Sala civica",
                "type": "Conferenza",
                "details": "Tema attuale",
                "event_date": "2026-06-15",
                "source_url": "",
            }
        ]
        msg = fetcher._format_event_message("Modena", date(2026, 6, 15), events)
        text_link_entities = [
            e for e in msg.entities if e.type == "text_link"
        ]
        assert len(text_link_entities) == 0

    def test_header_is_bold(self, db):
        fetcher = self._make_fetcher(db)
        events = [
            {
                "title": "Evento",
                "time": "12:00",
                "location": "Centro",
                "type": "Incontro",
                "event_date": "2026-06-15",
                "source_url": "https://example.com/e",
            }
        ]
        msg = fetcher._format_event_message("Modena", date(2026, 6, 15), events)
        bold_entities = [e for e in msg.entities if e.type == "bold"]
        assert len(bold_entities) >= 1

    def test_footer_is_italic(self, db):
        fetcher = self._make_fetcher(db)
        events = [
            {
                "title": "Evento",
                "time": "12:00",
                "location": "Centro",
                "type": "Incontro",
                "event_date": "2026-06-15",
                "source_url": "https://example.com/e",
            }
        ]
        msg = fetcher._format_event_message("Modena", date(2026, 6, 15), events)
        italic_entities = [e for e in msg.entities if e.type == "italic"]
        assert len(italic_entities) == 1
        ie = italic_entities[0]
        assert "Exa" in msg.text[ie.offset : ie.offset + ie.length]


class TestAdminSendCustomMessageRichPath:
    def test_valid_rich_json_queues_with_entities(self, admin_test_env):
        admin_app, db = admin_test_env
        db.add_user(1, "alice", "Roma")

        payload = json.dumps({
            "text": "Rich message with bold",
            "entities": [
                {"type": "bold", "offset": 6, "length": 7},
            ],
        })
        with admin_app.app.test_client() as client:
            response = client.post(
                "/send_custom_message/1",
                data={"message_text": payload},
                headers=_auth_header(),
                follow_redirects=True,
            )
            assert response.status_code == 200

        queued = db.get_pending_messages()
        assert len(queued) == 1
        assert queued[0].message == "Rich message with bold"
        assert queued[0].entities_json is not None

        entities_data = json.loads(queued[0].entities_json)
        assert len(entities_data) == 1
        assert entities_data[0]["type"] == "bold"

    def test_rich_json_entities_override_parse_mode_in_queue(
        self, admin_test_env
    ):
        admin_app, db = admin_test_env
        db.add_user(1, "alice", "Roma")

        payload = json.dumps({
            "text": "Entities override parse_mode",
            "parse_mode": "HTML",
            "entities": [
                {"type": "italic", "offset": 0, "length": 8},
            ],
        })
        with admin_app.app.test_client() as client:
            response = client.post(
                "/send_custom_message/1",
                data={"message_text": payload},
                headers=_auth_header(),
                follow_redirects=True,
            )
            assert response.status_code == 200

        queued = db.get_pending_messages()
        assert len(queued) == 1
        assert queued[0].parse_mode is None
        assert queued[0].entities_json is not None

    def test_invalid_json_like_payload_does_not_queue(self, admin_test_env):
        admin_app, db = admin_test_env
        db.add_user(1, "alice", "Roma")

        with admin_app.app.test_client() as client:
            response = client.post(
                "/send_custom_message/1",
                data={"message_text": "{broken json"},
                headers=_auth_header(),
                follow_redirects=True,
            )
            assert response.status_code == 200
            html = response.get_data(as_text=True)
            assert "Invalid rich message JSON" in html

        queued = db.get_pending_messages()
        assert len(queued) == 0

    def test_json_object_missing_text_does_not_queue(self, admin_test_env):
        admin_app, db = admin_test_env
        db.add_user(1, "alice", "Roma")

        payload = json.dumps({
            "entities": [{"type": "bold", "offset": 0, "length": 4}]
        })
        with admin_app.app.test_client() as client:
            response = client.post(
                "/send_custom_message/1",
                data={"message_text": payload},
                headers=_auth_header(),
                follow_redirects=True,
            )
            assert response.status_code == 200
            html = response.get_data(as_text=True)
            assert "Invalid rich message JSON" in html

        queued = db.get_pending_messages()
        assert len(queued) == 0

    def test_rich_json_with_link_preview_disabled(self, admin_test_env):
        admin_app, db = admin_test_env
        db.add_user(1, "alice", "Roma")

        payload = json.dumps({
            "text": "No preview please",
            "link_preview_options": {"is_disabled": True},
        })
        with admin_app.app.test_client() as client:
            response = client.post(
                "/send_custom_message/1",
                data={"message_text": payload},
                headers=_auth_header(),
                follow_redirects=True,
            )
            assert response.status_code == 200

        queued = db.get_pending_messages()
        assert len(queued) == 1
        assert queued[0].link_preview_options_json is not None
        lpo = json.loads(queued[0].link_preview_options_json)
        assert lpo.get("is_disabled") is True


class TestRichMessageStorageRoundtrip:
    def test_storage_roundtrip_with_entities(self):
        storage = RichMessageStorage(
            message="Bold text here",
            entities_json=json.dumps(
                [{"type": "bold", "offset": 0, "length": 4}]
            ),
        )
        msg = rich_message_from_queue_row(storage)
        assert msg.text == "Bold text here"
        assert msg.entities is not None
        assert len(msg.entities) == 1
        assert msg.entities[0].type == "bold"
        assert msg.entities[0].offset == 0
        assert msg.entities[0].length == 4

    def test_storage_roundtrip_with_link_preview(self):
        storage = RichMessageStorage(
            message="Preview disabled msg",
            link_preview_options_json=json.dumps({"is_disabled": True}),
        )
        msg = rich_message_from_queue_row(storage)
        assert msg.link_preview_options is not None
        assert msg.link_preview_options.is_disabled is True

    def test_storage_roundtrip_plain_text(self):
        storage = RichMessageStorage(message="Plain text only")
        msg = rich_message_from_queue_row(storage)
        assert msg.text == "Plain text only"
        assert msg.parse_mode is None
        assert msg.entities is None
        assert msg.link_preview_options is None
