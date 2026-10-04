from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time
from telegram import LinkPreviewOptions, MessageEntity

import partita_bot.config as config
import run_bot
from partita_bot.custom_bot import DeliveryResult
from partita_bot.event_fetcher import FETCH_FAILURE, EventFetcher
from partita_bot.notifications import process_notifications
from partita_bot.rich_text import RichMessage, rich_message_from_queue_row
from partita_bot.storage import Database, MessageQueue

FROZEN_UTC = datetime(2026, 3, 2, 6, 30, tzinfo=ZoneInfo("UTC"))
LOCAL_TIME = datetime(2026, 3, 2, 8, 0, tzinfo=ZoneInfo("Europe/Rome"))


@pytest.fixture
def frozen_local_time():
    with freeze_time(FROZEN_UTC, auto_tick_seconds=0.001):
        yield LOCAL_TIME


class MultiCityFetcher:
    def __init__(self, city_responses: dict[str, str | None]):
        self.city_responses = city_responses
        self.calls: list[str] = []

    def fetch_event_message(self, city: str, target_date: date) -> str | None:
        self.calls.append(city)
        return self.city_responses.get(city)


class RichCityFetcher:
    def __init__(self, city_responses: dict[str, RichMessage]):
        self.city_responses = city_responses
        self.calls: list[str] = []

    def fetch_event_message(self, city: str, target_date: date) -> RichMessage:
        self.calls.append(city)
        return self.city_responses[city]


def _user_last_notification(db: Database, telegram_id: int):
    user = db.get_user(telegram_id)
    assert user is not None
    return user.last_notification


def test_process_notifications_multi_city_onboarding_no_events_then_events(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Parma")
        db.set_user_cities(1, ["parma", "milano"])

        fetcher = MultiCityFetcher(city_responses={"Parma": None, "Milano": "Evento a Milano"})

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert summary["notifications_sent"] == 1
        assert summary["no_events"] == 1
        assert summary["already_notified"] == 0
        assert summary["fetch_errors"] == 0

        queued = db.get_pending_messages()
        assert len(queued) == 1
        assert queued[0].telegram_id == 1
        assert queued[0].message == "Evento a Milano"

        assert fetcher.calls == ["Parma", "Milano"]
        assert _user_last_notification(db, 1) is not None


def test_process_notifications_queues_each_eventful_city(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Parma")
        db.set_user_cities(1, ["parma", "milano"])

        fetcher = MultiCityFetcher(
            city_responses={"Parma": "Evento a Parma", "Milano": "Evento a Milano"}
        )

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert summary == {
            "notifications_sent": 2,
            "no_events": 0,
            "already_notified": 0,
            "fetch_errors": 0,
        }
        queued = db.get_pending_messages()
        assert [(row.telegram_id, row.message) for row in queued] == [
            (1, "Evento a Parma"),
            (1, "Evento a Milano"),
        ]
        assert fetcher.calls == ["Parma", "Milano"]
        assert _user_last_notification(db, 1) is not None


def test_process_notifications_queues_three_eventful_cities(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Parma")
        db.set_user_cities(1, ["parma", "milano", "torino"])

        fetcher = MultiCityFetcher(
            city_responses={
                "Parma": "Evento a Parma",
                "Milano": "Evento a Milano",
                "Torino": "Evento a Torino",
            }
        )

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert summary["notifications_sent"] == 3
        queued = db.get_pending_messages()
        assert [(row.telegram_id, row.message) for row in queued] == [
            (1, "Evento a Parma"),
            (1, "Evento a Milano"),
            (1, "Evento a Torino"),
        ]
        assert fetcher.calls == ["Parma", "Milano", "Torino"]


def test_process_notifications_shared_cities_respect_access(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.add_user(2, "bob", "Roma")
        db.add_user(3, "carla", "Roma")
        db.set_user_cities(1, ["roma", "milano"])
        db.set_user_cities(2, ["roma"])
        db.set_user_cities(3, ["roma"])
        db.add_to_list("blocklist", 3)

        fetcher = MultiCityFetcher(
            city_responses={"Roma": "Evento a Roma", "Milano": "Evento a Milano"}
        )

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert summary["notifications_sent"] == 3
        queued = db.get_pending_messages()
        assert [(row.telegram_id, row.message) for row in queued] == [
            (1, "Evento a Roma"),
            (2, "Evento a Roma"),
            (1, "Evento a Milano"),
        ]
        assert fetcher.calls == ["Roma", "Milano"]


def test_process_notifications_repeat_same_day_after_timestamp_update(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma", "milano"])

        fetcher = MultiCityFetcher(
            city_responses={"Roma": "Evento a Roma", "Milano": "Evento a Milano"}
        )

        first = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )
        second = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert first["notifications_sent"] == 2
        assert second == {
            "notifications_sent": 0,
            "no_events": 0,
            "already_notified": 1,
            "fetch_errors": 0,
        }
        assert len(db.get_pending_messages()) == 2
        assert fetcher.calls == ["Roma", "Milano", "Roma", "Milano"]


def test_process_notifications_already_notified_multi_city_user_counted_once(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.add_user(2, "bob", "Napoli")
        db.set_user_cities(1, ["roma", "milano", "torino"])
        db.set_user_cities(2, ["napoli"])

        alice = db.get_user(1)
        assert alice is not None
        alice.last_notification = datetime(2026, 3, 2, 6, 0, tzinfo=ZoneInfo("UTC"))
        db.session.commit()

        fetcher = MultiCityFetcher(
            city_responses={
                "Roma": "Evento a Roma",
                "Milano": "Evento a Milano",
                "Torino": "Evento a Torino",
                "Napoli": "Evento a Napoli",
            }
        )

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert summary["already_notified"] == 1
        assert summary["notifications_sent"] == 1
        queued = db.get_pending_messages()
        assert [(row.telegram_id, row.message) for row in queued] == [(2, "Evento a Napoli")]
        assert fetcher.calls == ["Roma", "Milano", "Torino", "Napoli"]


@pytest.mark.parametrize(
    ("city_order", "expected_calls"),
    [
        (["vuota", "guasta", "piena"], ["Vuota", "Guasta", "Piena"]),
        (["piena", "vuota", "guasta"], ["Piena", "Vuota", "Guasta"]),
    ],
)
def test_process_notifications_empty_and_failing_cities_do_not_suppress_valid_city(
    frozen_local_time, city_order, expected_calls
):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Parma")
        db.set_user_cities(1, city_order)

        fetcher = MultiCityFetcher(
            city_responses={
                "Vuota": None,
                "Guasta": FETCH_FAILURE,
                "Piena": "Evento a Piena",
            }
        )

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert summary["no_events"] == 1
        assert summary["fetch_errors"] == 1
        assert summary["notifications_sent"] == 1
        queued = db.get_pending_messages()
        assert [(row.telegram_id, row.message) for row in queued] == [(1, "Evento a Piena")]
        assert fetcher.calls == expected_calls


def test_process_notifications_queue_failure_in_one_city_keeps_other_city(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma", "milano"])

        fetcher = MultiCityFetcher(
            city_responses={"Roma": "Evento a Roma", "Milano": "Evento a Milano"}
        )
        attempts: list[tuple[int, str]] = []

        def queue_message(telegram_id: int, message) -> bool:
            attempts.append((telegram_id, str(message)))
            if str(message) == "Evento a Roma":
                return False
            return db.queue_rich_message(telegram_id, message)

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=queue_message,
            local_time=frozen_local_time,
        )

        assert summary["notifications_sent"] == 1
        assert attempts == [(1, "Evento a Roma"), (1, "Evento a Milano")]
        queued = db.get_pending_messages()
        assert [(row.telegram_id, row.message) for row in queued] == [(1, "Evento a Milano")]
        assert _user_last_notification(db, 1) is not None


def test_process_notifications_all_queue_failures_leave_timestamp_untouched(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma", "milano"])

        fetcher = MultiCityFetcher(
            city_responses={"Roma": "Evento a Roma", "Milano": "Evento a Milano"}
        )

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=lambda telegram_id, message: False,
            local_time=frozen_local_time,
        )

        assert summary["notifications_sent"] == 0
        assert db.get_pending_messages() == []
        assert _user_last_notification(db, 1) is None


def test_process_notifications_mark_manual_marks_both_timestamps(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma", "milano"])

        fetcher = MultiCityFetcher(
            city_responses={"Roma": "Evento a Roma", "Milano": "Evento a Milano"}
        )

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
            mark_manual=True,
        )

        user = db.get_user(1)
        assert user is not None
        assert summary["notifications_sent"] == 2
        assert user.last_notification is not None
        assert user.last_manual_notification is not None


def test_process_notifications_preserves_rich_metadata_for_each_city(frozen_local_time):
    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma", "milano"])

        roma_message = RichMessage(
            text="Concerto a Roma",
            entities=[
                MessageEntity("bold", 0, 8),
                MessageEntity("text_link", 9, 4, url="https://roma.example"),
            ],
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        milano_message = RichMessage(
            text="Mostra a Milano",
            entities=[MessageEntity("italic", 0, 6)],
            link_preview_options=LinkPreviewOptions(is_disabled=False),
        )
        fetcher = RichCityFetcher(city_responses={"Roma": roma_message, "Milano": milano_message})

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert summary["notifications_sent"] == 2
        queued = db.get_pending_messages()
        assert [row.message for row in queued] == ["Concerto a Roma", "Mostra a Milano"]

        restored_roma = rich_message_from_queue_row(queued[0])
        assert restored_roma.entities is not None
        assert [
            (entity.type, entity.offset, entity.length) for entity in restored_roma.entities
        ] == [
            ("bold", 0, 8),
            ("text_link", 9, 4),
        ]
        assert restored_roma.entities[1].url == "https://roma.example"
        assert restored_roma.link_preview_options is not None
        assert restored_roma.link_preview_options.is_disabled is True

        restored_milano = rich_message_from_queue_row(queued[1])
        assert restored_milano.entities is not None
        assert [
            (entity.type, entity.offset, entity.length) for entity in restored_milano.entities
        ] == [("italic", 0, 6)]
        assert restored_milano.link_preview_options is not None
        assert restored_milano.link_preview_options.is_disabled is False


ROMA_SOURCE_URL = "https://events.example/roma/opera"
MILANO_SOURCE_URL = "https://events.example/milano/jazz"


class FailOnNetworkSession:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def _fail(self, method: str, url: str) -> None:
        self.calls.append((method, url))
        raise AssertionError(f"unexpected {method} request to {url}")

    def get(self, url: str, **kwargs: object) -> None:
        self._fail("GET", url)

    def post(self, url: str, **kwargs: object) -> None:
        self._fail("POST", url)


class RecordingDeliveryBot:
    def __init__(self):
        self.calls: list[dict[str, object]] = []
        self._next_message_id = 700

    def send_message_sync(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
        entities: list[MessageEntity] | None = None,
        link_preview_options: LinkPreviewOptions | None = None,
    ) -> DeliveryResult:
        self._next_message_id += 1
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "entities": entities,
                "link_preview_options": link_preview_options,
                "message_id": self._next_message_id,
            }
        )
        return DeliveryResult(DeliveryResult.SENT, message_id=self._next_message_id)


def _seed_event_cache(db: Database, city: str, source_url: str) -> None:
    event_date = LOCAL_TIME.date()
    db.save_event_cache(
        city,
        event_date,
        "yes",
        [
            {
                "title": f"Evento speciale a {city}",
                "time": "21:00",
                "location": f"Teatro {city}",
                "type": "Concerto",
                "details": f"Dettagli per {city}",
                "event_date": event_date.isoformat(),
                "source_url": source_url,
            }
        ],
        query_type="general",
    )
    db.save_event_cache(city, event_date, "no", [], query_type="football")


def test_process_notifications_multi_city_end_to_end_rich_delivery(frozen_local_time, monkeypatch):
    monkeypatch.setattr(config, "FOOTBALL_API_TOKEN", "")

    with Database(database_url="sqlite:///:memory:") as db:
        db.add_user(1, "alice", "Roma")
        db.set_user_cities(1, ["roma", "milano"])
        _seed_event_cache(db, "roma", ROMA_SOURCE_URL)
        _seed_event_cache(db, "milano", MILANO_SOURCE_URL)

        http_session = FailOnNetworkSession()
        fetcher = EventFetcher(db, http_client=http_session)

        summary = process_notifications(
            users=db.get_all_users(),
            db=db,
            fetcher=fetcher,
            queue_message=db.queue_rich_message,
            local_time=frozen_local_time,
        )

        assert summary == {
            "notifications_sent": 2,
            "no_events": 0,
            "already_notified": 0,
            "fetch_errors": 0,
        }
        assert http_session.calls == []

        queued = db.get_pending_messages()
        assert [row.telegram_id for row in queued] == [1, 1]
        assert "a Roma ci sono 1 eventi rilevanti" in queued[0].message
        assert "a Milano ci sono 1 eventi rilevanti" in queued[1].message
        assert queued[0].entities_json is not None
        assert queued[1].entities_json is not None
        queued_ids = [row.id for row in queued]

        delivery_bot = RecordingDeliveryBot()
        run_bot.process_message_batch(delivery_bot, db, queued)

        assert len(delivery_bot.calls) == 2
        expected_urls = [ROMA_SOURCE_URL, MILANO_SOURCE_URL]
        for call, expected_url in zip(delivery_bot.calls, expected_urls):
            assert call["chat_id"] == 1
            assert call["parse_mode"] is None
            assert "🔗 Vai alla fonte" in call["text"]

            entities = call["entities"]
            assert entities is not None
            entity_types = [entity.type for entity in entities]
            assert entity_types.count(MessageEntity.BOLD) == 2
            assert entity_types.count(MessageEntity.BLOCKQUOTE) == 1
            link_entities = [
                entity for entity in entities if entity.type == MessageEntity.TEXT_LINK
            ]
            assert [entity.url for entity in link_entities] == [expected_url]

            utf16_length = len(call["text"].encode("utf-16-le")) // 2
            for entity in entities:
                assert 0 < entity.length
                assert entity.offset + entity.length <= utf16_length

            link_preview_options = call["link_preview_options"]
            assert link_preview_options is not None
            assert link_preview_options.is_disabled is True

        assert db.get_pending_messages() == []
        for message_id, call in zip(queued_ids, delivery_bot.calls):
            row = db.session.get(MessageQueue, message_id)
            assert row is not None
            assert row.sent is True
            assert row.sent_at is not None
            assert row.sent_message_id == call["message_id"]
