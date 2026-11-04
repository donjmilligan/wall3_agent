"""extract_inbox against a fake Slack that serves the inbox fixture's responses.

The fixture directory doubles as the fake server's data, so an end-to-end
extract -> normalize round trip can be checked against the same hand-written
expectations as tests/test_normalize_inbox.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import INBOX_FIXTURE, FakeResponse, FakeSession, make_client

from wall3_slack.client import SlackAuthError
from wall3_slack.extract import InboxLimits, extract_inbox, new_run_dir
from wall3_slack.normalize import normalize_dir

NOW = datetime(2026, 11, 4, 18, 0, 0, tzinfo=UTC)
OLDEST = datetime(2026, 11, 3, 18, 0, 0, tzinfo=UTC)


def _load(rel: str) -> dict[str, Any]:
    return json.loads((INBOX_FIXTURE / rel).read_text())


def _by_param(key: str, table: dict[str, str]):
    def respond(call: dict[str, Any]) -> FakeResponse:
        value = call["params"][key]
        if value not in table:
            return FakeResponse({"ok": False, "error": "channel_not_found"})
        return FakeResponse(_load(table[value]))

    return respond


def _search(call: dict[str, Any]) -> FakeResponse:
    query = call["params"]["query"]
    if query.startswith("from:"):
        return FakeResponse(_load("search.messages/my-messages/page-001.json"))
    return FakeResponse(_load("search.messages/mentions/page-001.json"))


def fixture_routes() -> dict[str, list[Any]]:
    return {
        "auth.test": [FakeResponse(_load("whoami.json"))],
        "conversations.list": [FakeResponse(_load("conversations.list/im-mpim-page-001.json"))],
        "conversations.history": [
            _by_param(
                "channel",
                {
                    "D000ALICE": "conversations.history/D000ALICE/page-001.json",
                    "G000MPIM": "conversations.history/G000MPIM/page-001.json",
                },
            )
        ],
        "search.messages": [_search],
        "conversations.replies": [
            _by_param(
                "ts",
                {
                    "1793793600.000100": (
                        "conversations.replies/C000GENERAL-1793793600.000100/page-001.json"
                    ),
                    "1793718000.000100": (
                        "conversations.replies/C000GENERAL-1793718000.000100/page-001.json"
                    ),
                },
            )
        ],
        "users.info": [
            _by_param(
                "user",
                {
                    "U000DON": "users.info/U000DON.json",
                    "U000ALICE": "users.info/U000ALICE.json",
                    "U000BOB": "users.info/U000BOB.json",
                },
            )
        ],
    }


def run_inbox(tmp_path: Path, limits: InboxLimits | None = None) -> tuple[Path, FakeSession]:
    client, session, _ = make_client(fixture_routes())
    out = new_run_dir(tmp_path / "raw", NOW)
    extract_inbox(client, out, oldest=OLDEST, now=NOW, since_label="24h", limits=limits)
    return out, session


def test_writes_whoami_verbatim(tmp_path):
    out, _ = run_inbox(tmp_path)

    assert json.loads((out / "whoami.json").read_text()) == _load("whoami.json")


def test_run_dir_is_a_utc_timestamp(tmp_path):
    out, _ = run_inbox(tmp_path)

    assert out == tmp_path / "raw" / "20261104T180000Z"
    assert new_run_dir(tmp_path / "raw", NOW) == tmp_path / "raw" / "20261104T180000Z-2"


def test_dm_history_uses_the_window_and_lists_only_dm_types(tmp_path):
    _, session = run_inbox(tmp_path)

    (listing,) = session.calls_to("conversations.list")
    assert listing["params"]["types"] == "im,mpim"
    history = session.calls_to("conversations.history")
    assert sorted(c["params"]["channel"] for c in history) == ["D000ALICE", "G000MPIM"]
    assert {c["params"]["oldest"] for c in history} == {"1793728800.000000"}


def test_search_queries_use_my_id_and_padded_after_dates(tmp_path):
    _, session = run_inbox(tmp_path)

    queries = [c["params"]["query"] for c in session.calls_to("search.messages")]
    # oldest 2026-11-03T18:00Z: mentions padded back 2 days; my-messages 7d lookback + 1 day.
    assert queries == ["<@U000DON> after:2026-11-01", "from:<@U000DON> after:2026-10-26"]
    assert {c["params"]["sort"] for c in session.calls_to("search.messages")} == {"timestamp"}


def test_fetches_threads_for_mentions_and_my_threads(tmp_path):
    out, session = run_inbox(tmp_path)

    fetched = [
        (c["params"]["channel"], c["params"]["ts"])
        for c in session.calls_to("conversations.replies")
    ]
    assert fetched == [
        ("C000GENERAL", "1793793600.000100"),  # the mention (mentions come first)
        ("C000GENERAL", "1793718000.000100"),  # the thread I started
    ]
    assert (
        out / "conversations.replies" / "C000GENERAL-1793718000.000100" / "page-001.json"
    ).is_file()


def test_resolves_every_referenced_user_once(tmp_path):
    out, session = run_inbox(tmp_path)

    users = [c["params"]["user"] for c in session.calls_to("users.info")]
    assert sorted(users) == ["U000ALICE", "U000BOB", "U000DON"]
    assert len(users) == len(set(users))
    assert sorted(p.name for p in (out / "users.info").iterdir()) == [
        "U000ALICE.json",
        "U000BOB.json",
        "U000DON.json",
    ]


def test_round_trip_normalizes_to_the_fixture_expectations(tmp_path):
    out, _ = run_inbox(tmp_path)

    by_id = {m.id: m for m in normalize_dir(out)}

    assert sorted(by_id) == sorted(
        [
            "D000ALICE:1793782800.000100",
            "D000ALICE:1793783100.000100",
            "D000ALICE:1793786400.000100",
            "G000MPIM:1793790000.000100",
            "G000MPIM:1793790600.000100",
            "C000GENERAL:1793793600.000100",
            "C000GENERAL:1793797200.000100",
            "C000GENERAL:1793799000.000100",
        ]
    )
    assert by_id["D000ALICE:1793786400.000100"].awaiting_reply is True
    assert by_id["G000MPIM:1793790000.000100"].awaiting_reply is False


def test_manifest_records_window_and_discovery(tmp_path):
    out, _ = run_inbox(tmp_path)

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["verb"] == "inbox"
    assert manifest["oldest"] == "1793728800.000000"
    assert manifest["since"] == "24h"
    assert manifest["discovery_only"] == ["search.messages/my-messages"]
    assert manifest["truncated"] == []
    assert manifest["calls"]["conversations.history"] == 2


def test_conversation_cap_limits_fan_out_and_reports_truncation(tmp_path):
    out, session = run_inbox(tmp_path, InboxLimits(max_conversations=1))

    assert len(session.calls_to("conversations.history")) == 1
    manifest = json.loads((out / "manifest.json").read_text())
    assert any("1 of 2 conversations beyond the cap of 1" in t for t in manifest["truncated"])


def test_thread_cap_limits_replies_and_reports_truncation(tmp_path):
    out, session = run_inbox(tmp_path, InboxLimits(max_threads=1))

    assert len(session.calls_to("conversations.replies")) == 1
    manifest = json.loads((out / "manifest.json").read_text())
    assert any("1 of 2 threads beyond the cap of 1" in t for t in manifest["truncated"])


def test_dm_thread_with_a_new_reply_is_fetched(tmp_path):
    routes = fixture_routes()
    history = _load("conversations.history/D000ALICE/page-001.json")
    parent = history["messages"][2]
    parent.update(
        {"thread_ts": parent["ts"], "reply_count": 1, "latest_reply": "1793790000.000300"}
    )
    routes["conversations.history"] = [
        lambda call: FakeResponse(
            history
            if call["params"]["channel"] == "D000ALICE"
            else _load("conversations.history/G000MPIM/page-001.json")
        )
    ]
    routes["conversations.replies"] = [FakeResponse({"ok": True, "messages": [parent]})]
    client, session, _ = make_client(routes)

    extract_inbox(client, tmp_path / "run", oldest=OLDEST, now=NOW, since_label="24h")

    fetched = [
        (c["params"]["channel"], c["params"]["ts"])
        for c in session.calls_to("conversations.replies")
    ]
    assert ("D000ALICE", "1793782800.000100") in fetched


def test_dm_thread_with_only_old_replies_is_not_fetched(tmp_path):
    """Paired with the test above: latest_reply before the window means no call."""
    routes = fixture_routes()
    history = _load("conversations.history/D000ALICE/page-001.json")
    parent = history["messages"][2]
    parent.update(
        {"thread_ts": parent["ts"], "reply_count": 1, "latest_reply": "1793700000.000000"}
    )
    routes["conversations.history"] = [
        lambda call: FakeResponse(
            history
            if call["params"]["channel"] == "D000ALICE"
            else _load("conversations.history/G000MPIM/page-001.json")
        )
    ]
    client, session, _ = make_client(routes)

    extract_inbox(client, tmp_path / "run", oldest=OLDEST, now=NOW, since_label="24h")

    fetched = [
        (c["params"]["channel"], c["params"]["ts"])
        for c in session.calls_to("conversations.replies")
    ]
    assert ("D000ALICE", "1793782800.000100") not in fetched
    assert len(fetched) == 2  # the two channel threads still are


def test_missing_thread_is_skipped_not_fatal(tmp_path):
    routes = fixture_routes()
    routes["conversations.replies"] = [FakeResponse({"ok": False, "error": "thread_not_found"})]
    client, _, _ = make_client(routes)

    extract_inbox(client, tmp_path / "run", oldest=OLDEST, now=NOW, since_label="24h")

    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert any("thread_not_found" in s for s in manifest["skipped"])
    assert (tmp_path / "run" / "whoami.json").is_file()


def test_missing_scope_on_search_is_fatal(tmp_path):
    routes = fixture_routes()
    routes["search.messages"] = [
        FakeResponse({"ok": False, "error": "missing_scope", "needed": "search:read"})
    ]
    client, _, _ = make_client(routes)

    with pytest.raises(SlackAuthError, match="search:read"):
        extract_inbox(client, tmp_path / "run", oldest=OLDEST, now=NOW, since_label="24h")
