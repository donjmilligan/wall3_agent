"""Hand-written check of normalize output against the shared Message contract.

Slice A validates the same output with a JSON Schema at integration time; this
is the local, dependency-free version. The field list and types below are
copied from the contract by hand -- deliberately *not* imported from
wall3_clickup.models, so a drift in models.py cannot pass by agreeing with
itself.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.conftest import INBOX_RAW
from wall3_clickup.cli import main

CONTRACT_KEYS = {
    "source", "id", "thread_id", "container", "author", "ts", "text",
    "permalink", "is_mention", "is_dm", "awaiting_reply",
}  # fmt: skip
CONTAINER_KINDS = {"channel", "dm", "mpim", "task", "chat"}
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def check_message(obj: dict) -> list[str]:
    problems: list[str] = []
    if set(obj) != CONTRACT_KEYS:
        problems.append(f"keys {sorted(set(obj) ^ CONTRACT_KEYS)} differ from the contract")
        return problems
    if obj["source"] not in ("slack", "clickup"):
        problems.append("source")
    if not isinstance(obj["id"], str) or not obj["id"]:
        problems.append("id must be a non-empty string")
    if obj["thread_id"] is not None and not isinstance(obj["thread_id"], str):
        problems.append("thread_id must be string or null")
    c = obj["container"]
    if not (isinstance(c, dict) and set(c) == {"kind", "id", "name"}):
        problems.append("container keys")
    elif c["kind"] not in CONTAINER_KINDS or not all(
        isinstance(c[k], str) for k in ("kind", "id", "name")
    ):
        problems.append("container values")
    a = obj["author"]
    if not (isinstance(a, dict) and set(a) == {"id", "name"}):
        problems.append("author keys")
    elif not (isinstance(a["id"], str) and isinstance(a["name"], str)):
        problems.append("author values must be strings")
    if not (isinstance(obj["ts"], str) and ISO_UTC.match(obj["ts"])):
        problems.append(f"ts {obj['ts']!r} is not ISO-8601 UTC")
    if not isinstance(obj["text"], str):
        problems.append("text")
    if not (isinstance(obj["permalink"], str) and obj["permalink"].startswith("https://")):
        problems.append("permalink")
    for flag in ("is_mention", "is_dm", "awaiting_reply"):
        if not isinstance(obj[flag], bool):
            problems.append(f"{flag} must be bool")
    return problems


def test_cli_normalize_json_lines_all_satisfy_the_contract(capsys):
    assert main(["normalize", str(INBOX_RAW), "--json"]) == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]

    assert len(lines) >= 3  # the integration check's floor
    assert len(lines) == 10
    for line in lines:
        obj = json.loads(line)
        assert check_message(obj) == [], (obj.get("id"), check_message(obj))
        assert obj["source"] == "clickup"


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (lambda o: o.update(extra=1), "differ from the contract"),
        (lambda o: o.update(id=5201), "id must be"),
        (lambda o: o.update(ts="2026-11-04 12:15:00"), "not ISO-8601"),
        (lambda o: o["container"].update(kind="list"), "container values"),
        (lambda o: o.update(is_dm="false"), "is_dm must be bool"),
        (lambda o: o["author"].update(id=1002), "author values"),
    ],
)
def test_the_checker_rejects_contract_violations(capsys, mutate, problem):
    """The checker above must be able to fail, or the green run means nothing."""
    main(["normalize", str(INBOX_RAW), "--json"])
    obj = json.loads(capsys.readouterr().out.splitlines()[0])
    assert check_message(obj) == []

    mutate(obj)

    assert any(problem in p for p in check_message(obj))


def test_whoami_json_shape(monkeypatch, capsys):
    from tests.conftest import FakeResponse, FakeSession

    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_TEAM_ID", "9000001")
    session = FakeSession(
        [
            FakeResponse(200, {"user": {"id": 1001, "username": "Don Example"}}),
            FakeResponse(200, {"teams": [{"id": "9000001", "name": "Example Workspace"}]}),
        ]
    )

    assert main(["whoami", "--json"], session=session) == 0

    assert json.loads(capsys.readouterr().out) == {
        "source": "clickup",
        "id": "1001",
        "name": "Don Example",
    }
