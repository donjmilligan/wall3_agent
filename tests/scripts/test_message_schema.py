"""docs/contracts/message.schema.json encodes the shared Message contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "docs/contracts/message.schema.json").read_text())
MESSAGE = Draft202012Validator(SCHEMA)
WHOAMI = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/whoami"})

FIELDS = [
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

SLACK: dict[str, Any] = {
    "source": "slack",
    "id": "D0TEST:1730729002.000100",
    "thread_id": "D0TEST:1730729000.000001",
    "container": {"kind": "dm", "id": "D0TEST", "name": "Alex Example"},
    "author": {"id": "U0TEST", "name": "Alex Example"},
    "ts": "2026-11-04T14:03:22Z",
    "text": "ping",
    "permalink": "https://example.slack.com/archives/D0TEST/p1730729002000100",
    "is_mention": False,
    "is_dm": True,
    "awaiting_reply": True,
}
CLICKUP: dict[str, Any] = {
    "source": "clickup",
    "id": "90000001",
    "thread_id": None,
    "container": {"kind": "chat", "id": "chan-1", "name": "Synthetic chat"},
    "author": {"id": "1001", "name": ""},
    "ts": "2026-11-04T15:00:00Z",
    "text": "",
    "permalink": "https://app.clickup.com/t/x",
    "is_mention": False,
    "is_dm": False,
    "awaiting_reply": False,
}


def valid(validator: Draft202012Validator, doc: Any) -> bool:
    return not list(validator.iter_errors(doc))


def test_schema_is_valid_2020_12():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize("doc", [SLACK, CLICKUP])
def test_good_messages_validate(doc):
    assert valid(MESSAGE, doc)


def test_required_fields_are_exactly_the_contract():
    assert sorted(SCHEMA["$defs"]["message"]["required"]) == sorted(FIELDS)


@pytest.mark.parametrize("field", FIELDS)
def test_each_field_is_required(field):
    doc = {k: v for k, v in SLACK.items() if k != field}
    assert not valid(MESSAGE, doc)


def mutate(base: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    doc = copy.deepcopy(base)
    target = doc
    *parents, last = path.split(".")
    for key in parents:
        target = target[key]
    target[last] = value
    return doc


@pytest.mark.parametrize(
    ("base", "path", "value"),
    [
        (SLACK, "extra", 1),
        (SLACK, "container.extra", 1),
        (SLACK, "author.extra", 1),
        (SLACK, "source", "email"),
        (SLACK, "ts", "2026-11-04T14:03:22+00:00"),
        (SLACK, "ts", "2026-11-04T14:03:22.5Z"),
        (SLACK, "ts", "2026-11-04 14:03:22Z"),
        (SLACK, "id", "D0TEST-1730729002"),
        (SLACK, "thread_id", "1730729000.000001"),
        (SLACK, "container.kind", "task"),
        (SLACK, "permalink", "slack://channel"),
        (SLACK, "is_mention", "true"),
        (SLACK, "author.id", ""),
        (CLICKUP, "container.kind", "channel"),
        (CLICKUP, "id", ""),
        (CLICKUP, "thread_id", ""),
    ],
)
def test_contract_violations_are_rejected(base, path, value):
    assert valid(MESSAGE, base)  # control: the unmutated document passes
    assert not valid(MESSAGE, mutate(base, path, value))


def test_whoami_shape():
    assert valid(WHOAMI, {"source": "slack", "id": "U0TEST", "name": "Don"})
    assert not valid(WHOAMI, {"source": "slack", "id": "U0TEST", "name": "Don", "x": 1})
    assert not valid(WHOAMI, {"source": "slack", "id": "U0TEST"})
    assert not valid(WHOAMI, {"source": "teams", "id": "U0TEST", "name": "Don"})
