from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest

from tests.helpers import jsonl, msg
from wall3_messaging.message import BadLine, Message, MessageError, message_from_dict, read_jsonl

REQUIRED = [
    "source",
    "id",
    "thread_id",
    "container",
    "author",
    "ts",
    "text",
    "permalink",
    "is_mention",
    "is_dm",
    "awaiting_reply",
]


def test_valid_message_parses_every_field() -> None:
    m = message_from_dict(
        msg(
            thread_id="CFAKE01:999.0001",
            is_dm=True,
            awaiting_reply=True,
            ts="2026-11-04T14:03:22Z",
        )
    )
    assert m.source == "slack"
    assert m.key == ("slack", "CFAKE01:1000.0001")
    assert m.thread_id == "CFAKE01:999.0001"
    assert m.container.name == "acme-dev"
    assert m.author.name == "Alice Example"
    assert m.ts == datetime(2026, 11, 4, 14, 3, 22, tzinfo=UTC)
    assert (m.is_mention, m.is_dm, m.awaiting_reply) == (False, True, True)


@pytest.mark.parametrize("field", REQUIRED)
def test_each_required_field_is_enforced(field: str) -> None:
    data = msg()
    message_from_dict(data)  # positive control: the unmodified fixture is valid
    del data[field]
    with pytest.raises(MessageError, match=rf"^{field}: missing"):
        message_from_dict(data)


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"source": "email"}, "source:"),
        ({"id": ""}, "id: must not be empty"),
        ({"thread_id": 5}, "thread_id: expected string or null"),
        ({"container": {"kind": "group", "id": "x", "name": "y"}}, "container.kind:"),
        ({"container": {"kind": "dm", "id": "x"}}, "container.name: missing"),
        ({"author": {"id": "U1"}}, "author.name: missing"),
        ({"author": "Alice"}, "author: expected object"),
        ({"is_dm": "true"}, "is_dm: expected true/false"),
        ({"awaiting_reply": 1}, "awaiting_reply: expected true/false"),
        ({"text": None}, "text: expected string"),
        ({"ts": "2026-11-04T12:00:00"}, "no timezone"),
        ({"ts": "yesterday"}, "not ISO-8601"),
    ],
)
def test_wrong_shapes_are_rejected_with_the_field_named(
    overrides: dict[str, object], needle: str
) -> None:
    with pytest.raises(MessageError, match=needle):
        message_from_dict(msg(**overrides))


def test_null_thread_id_is_allowed() -> None:
    assert message_from_dict(msg(thread_id=None)).thread_id is None


def test_non_utc_offset_is_converted_to_utc() -> None:
    m = message_from_dict(msg(ts="2026-11-04T09:00:00-05:00"))
    assert m.ts == datetime(2026, 11, 4, 14, 0, tzinfo=UTC)


def test_read_jsonl_reports_bad_lines_with_origin_and_line_number() -> None:
    text = jsonl(msg(id="a"), "{not json", "", "[1, 2]", msg(id="b"))
    items = list(read_jsonl(io.StringIO(text), "inbox.jsonl"))
    good = [i for i in items if isinstance(i, Message)]
    bad = [i for i in items if isinstance(i, BadLine)]
    assert [m.id for m in good] == ["a", "b"]
    assert [(b.origin, b.line_no) for b in bad] == [("inbox.jsonl", 2), ("inbox.jsonl", 4)]
    assert str(bad[0]).startswith("inbox.jsonl:2: invalid JSON")
    assert "expected a JSON object, got list" in bad[1].reason
