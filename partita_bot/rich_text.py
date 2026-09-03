from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from telegram import LinkPreviewOptions, MessageEntity

logger = logging.getLogger(__name__)

SUPPORTED_RICH_ENTITY_TYPES = frozenset(
    {MessageEntity.BOLD, MessageEntity.ITALIC, MessageEntity.TEXT_LINK, MessageEntity.BLOCKQUOTE}
)
ALLOWED_URL_SCHEMES = frozenset({"http", "https", "tg"})
MAX_RICH_NESTING_DEPTH = 4
NON_NESTABLE_RICH_ENTITY_TYPES = frozenset({MessageEntity.TEXT_LINK, MessageEntity.BLOCKQUOTE})


class _RichHtmlError(Exception):
    pass


@dataclass(slots=True)
class _RichNode:
    entity: MessageEntity
    start: int
    end: int
    url: str | None
    children: list["_RichNode"]


def _build_utf16_index_map(text: str) -> list[int]:
    mapping: list[int] = []
    for index, char in enumerate(text):
        mapping.append(index)
        if ord(char) > 0xFFFF:
            mapping.append(-1)
    mapping.append(len(text))
    return mapping


def _resolve_utf16_offset(mapping: list[int], offset: int) -> int:
    if offset < 0 or offset >= len(mapping):
        raise _RichHtmlError("entity offset or range is out of bounds")
    index = mapping[offset]
    if index < 0:
        raise _RichHtmlError("entity boundary splits a surrogate pair")
    return index


def _validate_text_link_url(url: Any) -> str:
    if not isinstance(url, str) or not url:
        raise _RichHtmlError("text_link entity has no url")
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in url):
        raise _RichHtmlError("text_link url contains unsafe characters")
    if "\\" in url:
        raise _RichHtmlError("text_link url contains a backslash")
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise _RichHtmlError("text_link url scheme is not allowed")
    if scheme in {"http", "https"}:
        if not parsed.hostname:
            raise _RichHtmlError("text_link http url has no host")
        try:
            port = parsed.port
        except ValueError:
            raise _RichHtmlError("text_link http url has an invalid port") from None
        if port is not None and port <= 0:
            raise _RichHtmlError("text_link http url has an invalid port")
    if scheme == "tg" and not parsed.netloc:
        raise _RichHtmlError("text_link tg url has no target")
    return html.escape(url, quote=True)


def _entity_opening_tag(entity: MessageEntity, url: str | None) -> str:
    if entity.type == MessageEntity.BOLD:
        return "<b>"
    if entity.type == MessageEntity.ITALIC:
        return "<i>"
    if entity.type == MessageEntity.TEXT_LINK:
        return f'<a href="{url}">'
    return "<blockquote>"


def _entity_closing_tag(entity: MessageEntity) -> str:
    if entity.type == MessageEntity.BOLD:
        return "</b>"
    if entity.type == MessageEntity.ITALIC:
        return "</i>"
    if entity.type == MessageEntity.TEXT_LINK:
        return "</a>"
    return "</blockquote>"


def _build_entity_forest(text: str, entities: list[Any]) -> list[_RichNode]:
    mapping = _build_utf16_index_map(text)
    spans: list[tuple[int, int, MessageEntity, str | None]] = []
    for entity in entities:
        if not isinstance(entity, MessageEntity):
            raise _RichHtmlError("entity is not a MessageEntity")
        if entity.type not in SUPPORTED_RICH_ENTITY_TYPES:
            raise _RichHtmlError(f"unsupported entity type {entity.type}")
        if not isinstance(entity.offset, int) or not isinstance(entity.length, int):
            raise _RichHtmlError("entity offset or length is not an integer")
        if entity.length <= 0:
            raise _RichHtmlError("entity length is not positive")
        start = _resolve_utf16_offset(mapping, entity.offset)
        end = _resolve_utf16_offset(mapping, entity.offset + entity.length)
        if end <= start:
            raise _RichHtmlError("entity range is empty")
        url = (
            _validate_text_link_url(entity.url)
            if entity.type == MessageEntity.TEXT_LINK
            else None
        )
        spans.append((start, end, entity, url))

    spans.sort(key=lambda span: (span[0], -span[1]))
    forest: list[_RichNode] = []
    stack: list[_RichNode] = []
    for start, end, entity, url in spans:
        node = _RichNode(entity=entity, start=start, end=end, url=url, children=[])
        while stack and node.start >= stack[-1].end:
            stack.pop()
        if stack:
            parent = stack[-1]
            if node.end > parent.end:
                raise _RichHtmlError("entities cross each other")
            parent_type = parent.entity.type
            if entity.type == MessageEntity.BLOCKQUOTE and parent_type != MessageEntity.BLOCKQUOTE:
                raise _RichHtmlError("blockquote nested inside an inline entity")
            ancestor_types = {ancestor.entity.type for ancestor in stack}
            if entity.type in NON_NESTABLE_RICH_ENTITY_TYPES and entity.type in ancestor_types:
                raise _RichHtmlError(f"{entity.type} entity nested inside a {entity.type} entity")
            if len(stack) + 1 > MAX_RICH_NESTING_DEPTH:
                raise _RichHtmlError("entity nesting is too deep")
            parent.children.append(node)
        else:
            forest.append(node)
        stack.append(node)
    return forest


def _emit_rich_html(text: str, nodes: list[_RichNode], start: int, end: int) -> str:
    pieces: list[str] = []
    cursor = start
    for node in nodes:
        pieces.append(html.escape(text[cursor : node.start]))
        pieces.append(_entity_opening_tag(node.entity, node.url))
        pieces.append(_emit_rich_html(text, node.children, node.start, node.end))
        pieces.append(_entity_closing_tag(node.entity))
        cursor = node.end
    pieces.append(html.escape(text[cursor:end]))
    return "".join(pieces)


def build_rich_html(text: str, entities: list[MessageEntity] | None) -> str:
    if not entities:
        return html.escape(text)
    try:
        forest = _build_entity_forest(text, entities)
    except Exception as exc:
        logger.warning("Rich HTML serialization fell back to plain text: %s", exc)
        return html.escape(text)
    return _emit_rich_html(text, forest, 0, len(text))


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
