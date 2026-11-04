from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.conftest import FakeResponse, FakeSession
from wall3_clickup.client import ClickUpClient
from wall3_clickup.config import ClickUpConfig
from wall3_clickup.extract import RawWriter, extract_inbox
from wall3_clickup.extract.raw_writer import safe_component

V2 = "https://api.clickup.com/api/v2"
V3 = "https://api.clickup.com/api/v3"
SINCE = 1_793_793_600_000

ROUTES: dict[tuple[str, str], object] = {
    ("GET", f"{V2}/user"): {"user": {"id": 1001, "username": "Don Example"}},
    ("GET", f"{V2}/team"): {"teams": [{"id": "9000001", "members": []}]},
    ("GET", f"{V2}/team/9000001/task"): {"tasks": [{"id": "abc123", "date_updated": "1"}]},
    ("GET", f"{V2}/task/abc123/comment"): {
        "comments": [
            {"id": "11", "date": str(SINCE + 1), "reply_count": "2", "user": {"id": 1002}},
            {"id": "12", "date": str(SINCE + 2), "reply_count": "0", "user": {"id": 1002}},
        ]
    },
    ("GET", f"{V2}/comment/11/reply"): {"comments": [{"id": "13", "date": str(SINCE + 3)}]},
    ("GET", f"{V3}/workspaces/9000001/chat/channels"): {
        "next_cursor": "",
        "data": [{"id": "ch1", "type": "CHANNEL"}],
    },
    ("GET", f"{V3}/workspaces/9000001/chat/channels/ch1/messages"): {
        "next_cursor": "",
        "data": [
            {"id": "a", "user_id": "1002", "content": "@Don hi", "date": SINCE + 5},
            {"id": "b", "user_id": "1002", "content": "no at sign", "date": SINCE + 4},
            {"id": "c", "user_id": "1001", "content": "@Alice mine", "date": SINCE + 3},
            {"id": "d", "user_id": "1002", "content": "@Don old", "date": SINCE - 5},
        ],
    },
    ("GET", f"{V3}/workspaces/9000001/chat/messages/a/tagged_users"): {"data": [{"id": "1001"}]},
}


def _client(routes) -> tuple[ClickUpClient, FakeSession]:
    def route(method, url, params):  # noqa: ANN001
        if (method, url) in routes:
            return FakeResponse(200, routes[(method, url)])
        return FakeResponse(404, {"err": "no route"})

    session = FakeSession(router=route)
    client = ClickUpClient(ClickUpConfig(token="t", team_id="9000001"), session=session)
    return client, session


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_inbox_writes_every_response_verbatim(tmp_path):
    client, session = _client(ROUTES)
    writer = RawWriter.create(tmp_path, now=datetime(2026, 11, 4, 16, 0, tzinfo=UTC))

    raw = extract_inbox(client, writer, team_id="9000001", since_ms=SINCE)

    assert raw == tmp_path / "20261104T160000Z"
    assert _load(raw / "whoami.json") == ROUTES[("GET", f"{V2}/user")]
    assert _load(raw / "teams.json") == ROUTES[("GET", f"{V2}/team")]
    assert _load(raw / "task_pages/page-000.json") == ROUTES[("GET", f"{V2}/team/9000001/task")]
    assert (
        _load(raw / "comments/abc123/page-000.json") == ROUTES[("GET", f"{V2}/task/abc123/comment")]
    )
    assert _load(raw / "replies/11.json") == ROUTES[("GET", f"{V2}/comment/11/reply")]
    assert not (raw / "replies/12.json").exists()  # reply_count 0 -> no request
    assert _load(raw / "chat/messages/ch1/page-000.json")["data"][0]["id"] == "a"
    manifest = _load(raw / "manifest.json")
    assert (manifest["kind"], manifest["since_ms"], manifest["team_id"]) == (
        "inbox",
        SINCE,
        "9000001",
    )
    assert not {"POST", "PUT", "DELETE"} & set(session.methods)


def test_tagged_users_fetched_only_for_possible_mentions_in_window(tmp_path):
    client, session = _client(ROUTES)

    raw = extract_inbox(client, RawWriter.create(tmp_path), team_id="9000001", since_ms=SINCE)

    tagged = sorted(p.stem for p in (raw / "chat/tagged_users").glob("*.json"))
    # a: has "@", not mine, in window. b: no "@". c: mine. d: before the window.
    assert tagged == ["a"]
    tagged_calls = [c for c in session.calls if c["url"].endswith("/tagged_users")]
    assert len(tagged_calls) == 1


def test_chat_failure_does_not_lose_the_task_half(tmp_path):
    routes = {k: v for k, v in ROUTES.items() if "/chat/" not in k[1]}
    client, _ = _client(routes)

    raw = extract_inbox(client, RawWriter.create(tmp_path), team_id="9000001", since_ms=SINCE)

    assert (raw / "comments/abc123/page-000.json").is_file()
    notes = _load(raw / "manifest.json")["notes"]
    assert any(n.startswith("chat skipped") for n in notes)


def test_raw_writer_never_overwrites_a_snapshot(tmp_path):
    now = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)

    first = RawWriter.create(tmp_path, now=now).directory
    second = RawWriter.create(tmp_path, now=now).directory

    assert first != second
    assert second.name == "20261104T160000Z-1"


# ---------------------------------------------------------------- path components


@pytest.mark.parametrize("value", ["abc123", "86abc", "C-1", "a_b.c:d"])
def test_safe_component_accepts_api_ids(value):
    assert safe_component(value) == value


@pytest.mark.parametrize("value", ["..", ".", "../x", "a/b", "", "a b", "x\n", "x\r\n"])
def test_safe_component_refuses_path_like_values(value):
    with pytest.raises(ValueError, match="path component"):
        safe_component(value)
