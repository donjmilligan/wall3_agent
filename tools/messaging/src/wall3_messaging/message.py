"""The Message contract (docs/contracts/message.schema.json) and a strict JSONL reader.

Validation is hand-written and stdlib-only on purpose: the package has no runtime
dependencies, and every rejection names the field and the reason so a bad line is
reported, never silently dropped.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SOURCES = frozenset({"slack", "clickup"})
CONTAINER_KINDS = frozenset({"channel", "dm", "mpim", "task", "chat"})
BOOL_FIELDS = ("is_mention", "is_dm", "awaiting_reply")
STR_FIELDS = ("id", "text", "permalink")


class MessageError(ValueError):
    """A line that does not satisfy the Message contract."""


@dataclass(frozen=True)
class Container:
    kind: str
    id: str
    name: str


@dataclass(frozen=True)
class Author:
    id: str
    name: str


@dataclass(frozen=True)
class Message:
    source: str
    id: str
    thread_id: str | None
    container: Container
    author: Author
    ts: datetime
    text: str
    permalink: str
    is_mention: bool
    is_dm: bool
    awaiting_reply: bool
    raw: dict[str, Any]

    @property
    def key(self) -> tuple[str, str]:
        """Identity for de-duplication: (source, id)."""
        return (self.source, self.id)


@dataclass(frozen=True)
class BadLine:
    origin: str
    line_no: int
    reason: str

    def __str__(self) -> str:
        return f"{self.origin}:{self.line_no}: {self.reason}"


def parse_ts(value: object, field: str = "ts") -> datetime:
    """Parse an ISO-8601 timestamp that carries a timezone; return it in UTC."""
    if not isinstance(value, str) or not value:
        raise MessageError(f"{field}: expected a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MessageError(f"{field}: not ISO-8601 ({value!r})") from exc
    if parsed.tzinfo is None:
        raise MessageError(f"{field}: timestamp has no timezone ({value!r}); expected UTC 'Z'")
    return parsed.astimezone(UTC)


def _require_str(obj: dict[str, Any], field: str, prefix: str = "") -> str:
    if field not in obj:
        raise MessageError(f"{prefix}{field}: missing")
    value = obj[field]
    if not isinstance(value, str):
        raise MessageError(f"{prefix}{field}: expected string, got {type(value).__name__}")
    return value


def _require_obj(obj: dict[str, Any], field: str) -> dict[str, Any]:
    if field not in obj:
        raise MessageError(f"{field}: missing")
    value = obj[field]
    if not isinstance(value, dict):
        raise MessageError(f"{field}: expected object, got {type(value).__name__}")
    return value  # pyright: ignore[reportUnknownVariableType]


def message_from_dict(obj: object) -> Message:
    """Validate one decoded JSON value against the Message contract."""
    if not isinstance(obj, dict):
        raise MessageError(f"expected a JSON object, got {type(obj).__name__}")
    data: dict[str, Any] = obj  # pyright: ignore[reportUnknownVariableType]

    source = _require_str(data, "source")
    if source not in SOURCES:
        raise MessageError(f"source: {source!r} is not one of {sorted(SOURCES)}")
    for field in STR_FIELDS:
        _require_str(data, field)
    if not data["id"]:
        raise MessageError("id: must not be empty")

    if "thread_id" not in data:
        raise MessageError("thread_id: missing (use null when there is no thread)")
    thread_id = data["thread_id"]
    if thread_id is not None and not isinstance(thread_id, str):
        raise MessageError(f"thread_id: expected string or null, got {type(thread_id).__name__}")

    container_raw = _require_obj(data, "container")
    kind = _require_str(container_raw, "kind", "container.")
    if kind not in CONTAINER_KINDS:
        raise MessageError(f"container.kind: {kind!r} is not one of {sorted(CONTAINER_KINDS)}")
    container = Container(
        kind=kind,
        id=_require_str(container_raw, "id", "container."),
        name=_require_str(container_raw, "name", "container."),
    )

    author_raw = _require_obj(data, "author")
    author = Author(
        id=_require_str(author_raw, "id", "author."),
        name=_require_str(author_raw, "name", "author."),
    )

    flags: dict[str, bool] = {}
    for field in BOOL_FIELDS:
        if field not in data:
            raise MessageError(f"{field}: missing")
        value = data[field]
        if not isinstance(value, bool):
            raise MessageError(f"{field}: expected true/false, got {type(value).__name__}")
        flags[field] = value
    if "ts" not in data:
        raise MessageError("ts: missing")

    return Message(
        source=source,
        id=data["id"],
        thread_id=thread_id,
        container=container,
        author=author,
        ts=parse_ts(data.get("ts")),
        text=data["text"],
        permalink=data["permalink"],
        is_mention=flags["is_mention"],
        is_dm=flags["is_dm"],
        awaiting_reply=flags["awaiting_reply"],
        raw=data,
    )


def read_jsonl(lines: Iterable[str], origin: str) -> Iterator[Message | BadLine]:
    """Yield a Message per valid line and a BadLine per invalid one. Blank lines are skipped."""
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError as exc:
            yield BadLine(origin, line_no, f"invalid JSON ({exc.msg})")
            continue
        try:
            yield message_from_dict(decoded)
        except MessageError as exc:
            yield BadLine(origin, line_no, str(exc))
