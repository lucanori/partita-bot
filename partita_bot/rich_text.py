from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from telegram import LinkPreviewOptions, MessageEntity


@dataclass(slots=True)
class RichMessage:
    text: str
    parse_mode: str | None = None
    entities: list[MessageEntity] | None = None
    link_preview_options: LinkPreviewOptions | None = None

    @classmethod
    def from_plain(cls, text: str) -> RichMessage:
        return cls(text=text)

    @classmethod
    def from_json(cls, data: str | dict[str, Any]) -> RichMessage:
        if isinstance(data, str):
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                raise ValueError("RichMessage JSON payload must be valid JSON")
        else:
            payload = data
        if not isinstance(payload, dict):
            raise ValueError("RichMessage JSON payload must be a JSON object")
        text = payload.get("text")
        if not text or not isinstance(text, str):
            raise ValueError("RichMessage JSON payload requires a 'text' field")
        parse_mode = payload.get("parse_mode")
        entities_raw = payload.get("entities")
        link_preview_raw = payload.get("link_preview_options")

        entities = None
        if entities_raw is not None:
            if not isinstance(entities_raw, list):
                raise ValueError("RichMessage JSON 'entities' must be a list")
            entities = []
            for i, item in enumerate(entities_raw):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"RichMessage JSON 'entities' item {i} must be a JSON object"
                    )
                entity_type = item.get("type")
                if not entity_type:
                    raise ValueError(
                        f"RichMessage JSON 'entities' item {i} must have a 'type' field"
                    )
                offset = item.get("offset", 0)
                length = item.get("length", 0)
                kwargs: dict[str, Any] = {}
                if "url" in item:
                    kwargs["url"] = item["url"]
                if "user" in item:
                    kwargs["user"] = item["user"]
                if "language" in item:
                    kwargs["language"] = item["language"]
                if "custom_emoji_id" in item:
                    kwargs["custom_emoji_id"] = item["custom_emoji_id"]
                try:
                    entities.append(MessageEntity(entity_type, offset, length, **kwargs))
                except Exception as exc:
                    raise ValueError(
                        f"RichMessage JSON 'entities' item {i}: {exc}"
                    ) from exc

        if parse_mode and entities:
            parse_mode = None

        link_preview_options = None
        if link_preview_raw is not None:
            if not isinstance(link_preview_raw, dict):
                raise ValueError("RichMessage JSON 'link_preview_options' must be a JSON object")
            link_preview_options = LinkPreviewOptions(**link_preview_raw)

        return cls(
            text=text,
            parse_mode=parse_mode,
            entities=entities if entities else None,
            link_preview_options=link_preview_options,
        )


class RichMessageBuilder:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self._entities: list[MessageEntity] = []

    def _utf16_len(self, text: str) -> int:
        return len(text.encode("utf-16-le")) // 2

    def _offset(self) -> int:
        return sum(self._utf16_len(p) for p in self._parts)

    def add(self, text: str) -> RichMessageBuilder:
        self._parts.append(text)
        return self

    def add_bold(self, text: str) -> RichMessageBuilder:
        offset = self._offset()
        self._parts.append(text)
        self._entities.append(MessageEntity(MessageEntity.BOLD, offset, self._utf16_len(text)))
        return self

    def add_italic(self, text: str) -> RichMessageBuilder:
        offset = self._offset()
        self._parts.append(text)
        self._entities.append(MessageEntity(MessageEntity.ITALIC, offset, self._utf16_len(text)))
        return self

    def add_link(self, label: str, url: str) -> RichMessageBuilder:
        offset = self._offset()
        self._parts.append(label)
        self._entities.append(
            MessageEntity(MessageEntity.TEXT_LINK, offset, self._utf16_len(label), url=url)
        )
        return self

    def add_blockquote(self, text: str) -> RichMessageBuilder:
        offset = self._offset()
        self._parts.append(text)
        length = self._utf16_len(text)
        self._entities.append(MessageEntity(MessageEntity.BLOCKQUOTE, offset, length))
        return self

    def build(
        self, link_preview_options: LinkPreviewOptions | None = None
    ) -> RichMessage:
        return RichMessage(
            text="".join(self._parts),
            entities=self._entities if self._entities else None,
            link_preview_options=link_preview_options,
        )


@dataclass(slots=True)
class RichMessageStorage:
    message: str
    parse_mode: str | None = None
    entities_json: str | None = None
    link_preview_options_json: str | None = None


def deserialize_entities(entities_json: str | None) -> list[MessageEntity] | None:
    if not entities_json:
        return None
    raw = json.loads(entities_json)
    if not raw:
        return None
    entities: list[MessageEntity] = []
    for item in raw:
        entity_type = item.pop("type")
        kwargs: dict[str, Any] = {}
        for key in ("url", "language", "custom_emoji_id"):
            if key in item:
                kwargs[key] = item.pop(key)
        entities.append(MessageEntity(entity_type, item["offset"], item["length"], **kwargs))
    return entities


def deserialize_link_preview_options(json_str: str | None) -> LinkPreviewOptions | None:
    if not json_str:
        return None
    raw = json.loads(json_str)
    if not raw:
        return None
    return LinkPreviewOptions(**raw)


def rich_message_from_queue_row(row) -> RichMessage:
    return RichMessage(
        text=str(row.message),
        parse_mode=str(row.parse_mode) if row.parse_mode else None,
        entities=deserialize_entities(
            str(row.entities_json) if row.entities_json else None
        ),
        link_preview_options=deserialize_link_preview_options(
            str(row.link_preview_options_json) if row.link_preview_options_json else None
        ),
    )
