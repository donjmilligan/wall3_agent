"""The contract, restated by hand, checked against real CLI output.

This deliberately does not import the field list from ``models.py``: the point
is to catch the code drifting from the contract shared with the other slices.
"""

from __future__ import annotations

import json
import re

from wall3_slack.cli import main

TOP_LEVEL = {
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
}
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _check(record: dict) -> None:
    assert set(record) == TOP_LEVEL
    assert record["source"] == "slack"
    assert isinstance(record["id"], str) and re.match(r"^[A-Z0-9]+:\d+\.\d+$", record["id"])
    assert record["thread_id"] is None or re.match(r"^[A-Z0-9]+:\d+\.\d+$", record["thread_id"])
    assert set(record["container"]) == {"kind", "id", "name"}
    assert record["container"]["kind"] in {"channel", "dm", "mpim"}
    assert all(isinstance(record["container"][k], str) for k in ("id", "name"))
    assert record["container"]["name"]
    assert set(record["author"]) == {"id", "name"}
    assert all(isinstance(record["author"][k], str) and record["author"][k] for k in ("id", "name"))
    assert ISO_UTC.match(record["ts"])
    assert isinstance(record["text"], str)
    assert record["permalink"].startswith("https://")
    for flag in ("is_mention", "is_dm", "awaiting_reply"):
        assert type(record[flag]) is bool


def test_normalize_json_lines_match_the_contract(inbox_fixture, capsys):
    assert main(["normalize", str(inbox_fixture), "--json"]) == 0

    lines = capsys.readouterr().out.splitlines()

    assert len(lines) >= 4
    for line in lines:
        _check(json.loads(line))
    # The id's channel half is the container id, per the contract.
    for line in lines:
        record = json.loads(line)
        assert record["id"].split(":")[0] == record["container"]["id"]


def test_check_rejects_an_extra_field(inbox_fixture, capsys):
    """The checker itself must be able to fail, or the test above proves nothing."""
    main(["normalize", str(inbox_fixture), "--json"])
    record = json.loads(capsys.readouterr().out.splitlines()[0])
    record["extra"] = 1

    try:
        _check(record)
    except AssertionError:
        return
    raise AssertionError("contract check accepted an extra field")
