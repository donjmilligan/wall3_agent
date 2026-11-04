"""Normalize the synthetic inbox fixture and pin every scenario by hand.

Expected values below were written from the fixture files and Slack's
documented formats -- timestamps converted independently (epoch
1793782800 is 2026-11-04T09:00:00Z), permalinks per chat.getPermalink's
documented shape -- never by running the code under test.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from wall3_slack.models import Message
from wall3_slack.normalize import NormalizeError, normalize_dir


@pytest.fixture
def by_id(inbox_fixture: Path) -> dict[str, Message]:
    return {m.id: m for m in normalize_dir(inbox_fixture)}


def test_emits_exactly_the_in_window_human_and_bot_messages(by_id):
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


def test_output_is_chronological(inbox_fixture):
    stamps = [m.ts for m in normalize_dir(inbox_fixture)]
    assert stamps == [
        "2026-11-04T09:00:00Z",
        "2026-11-04T09:05:00Z",
        "2026-11-04T10:00:00Z",
        "2026-11-04T11:00:00Z",
        "2026-11-04T11:10:00Z",
        "2026-11-04T12:00:00Z",
        "2026-11-04T13:00:00Z",
        "2026-11-04T13:30:00Z",
    ]


def test_dm_awaiting_my_reply(by_id):
    msg = by_id["D000ALICE:1793786400.000100"]
    assert msg.container.kind == "dm"
    assert msg.container.name == "@Alice Example"
    assert msg.author.id == "U000ALICE"
    assert msg.author.name == "Alice Example"
    assert msg.ts == "2026-11-04T10:00:00Z"
    assert msg.thread_id is None
    assert msg.is_dm is True
    assert msg.is_mention is False
    assert msg.awaiting_reply is True
    assert msg.permalink == "https://example.slack.com/archives/D000ALICE/p1793786400000100"


def test_dm_where_i_replied_last_is_not_awaiting(by_id):
    bob = by_id["G000MPIM:1793790000.000100"]
    mine = by_id["G000MPIM:1793790600.000100"]
    assert bob.container.kind == "mpim"
    assert bob.container.name == "alice, bob, don"
    assert bob.is_dm is True
    assert bob.awaiting_reply is False
    assert mine.author.id == "U000DON"
    assert mine.awaiting_reply is False


def test_my_own_dm_message_is_never_awaiting(by_id):
    mine = by_id["D000ALICE:1793783100.000100"]
    assert mine.author.name == "Don Example"
    assert mine.is_dm is True
    assert mine.awaiting_reply is False


def test_channel_mention_awaiting_reply(by_id):
    msg = by_id["C000GENERAL:1793793600.000100"]
    assert msg.container.kind == "channel"
    assert msg.container.name == "#general"
    assert msg.is_mention is True
    assert msg.is_dm is False
    assert msg.awaiting_reply is True
    assert msg.text == "@Don Example can you confirm the on-call schedule? Q&A is in #general at 3"
    assert msg.permalink == "https://example.slack.com/archives/C000GENERAL/p1793793600000100"


def test_thread_reply_in_a_thread_i_started(by_id):
    msg = by_id["C000GENERAL:1793797200.000100"]
    assert msg.thread_id == "C000GENERAL:1793718000.000100"
    assert msg.author.name == "Bob Example"
    assert msg.text == "Left comments on section 2"
    # Not a DM and no @-mention of me, so by the contract it is not awaiting.
    assert msg.is_mention is False
    assert msg.awaiting_reply is False
    assert msg.permalink == (
        "https://example.slack.com/archives/C000GENERAL/p1793797200000100"
        "?thread_ts=1793718000.000100&cid=C000GENERAL"
    )


def test_thread_parent_before_the_window_is_context_not_output(by_id):
    assert "C000GENERAL:1793718000.000100" not in by_id
    # ...while its in-window reply, from the same replies file, is emitted.
    assert "C000GENERAL:1793797200.000100" in by_id


def test_discovery_only_search_is_not_emitted_even_inside_the_window(inbox_fixture, tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(inbox_fixture, raw)
    page = raw / "search.messages" / "my-messages" / "page-001.json"
    body = json.loads(page.read_text())
    body["messages"]["matches"][0]["ts"] = "1793800000.000100"  # move it into the window
    page.write_text(json.dumps(body))

    ids = {m.id for m in normalize_dir(raw)}

    assert "C000GENERAL:1793800000.000100" not in ids
    # Same file, discovery flag removed from the manifest: now it is emitted.
    manifest = json.loads((raw / "manifest.json").read_text())
    manifest["discovery_only"] = []
    (raw / "manifest.json").write_text(json.dumps(manifest))
    assert "C000GENERAL:1793800000.000100" in {m.id for m in normalize_dir(raw)}


def test_mrkdwn_link_and_user_mention_rendered(by_id):
    msg = by_id["D000ALICE:1793786400.000100"]
    assert msg.text == (
        "Can you review the deploy plan (https://example.com/plan) before noon? cc @Bob Example"
    )
    # Mentioning Bob is not mentioning me.
    assert msg.is_mention is False


def test_bot_message_kept_with_bot_author(by_id):
    msg = by_id["C000GENERAL:1793799000.000100"]
    assert msg.author.id == "B000BOT"
    assert msg.author.name == "reminders"
    assert msg.thread_id == "C000GENERAL:1793718000.000100"
    assert msg.awaiting_reply is False


def test_system_join_message_is_dropped(by_id):
    assert "G000MPIM:1793788200.000100" not in by_id
    assert "G000MPIM:1793790000.000100" in by_id  # same history page, kept


def test_earlier_dm_message_follows_the_literal_contract(by_id):
    """Alice spoke last in the DM's top-level stream, so her earlier message is awaiting too."""
    assert by_id["D000ALICE:1793782800.000100"].awaiting_reply is True


def test_a_later_reply_from_me_clears_awaiting(inbox_fixture, tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(inbox_fixture, raw)
    page = raw / "conversations.history" / "D000ALICE" / "page-001.json"
    body = json.loads(page.read_text())
    body["messages"].insert(
        0, {"type": "message", "user": "U000DON", "text": "Done.", "ts": "1793790000.000200"}
    )
    page.write_text(json.dumps(body))

    by_id = {m.id: m for m in normalize_dir(raw)}

    assert by_id["D000ALICE:1793786400.000100"].awaiting_reply is False
    assert by_id["D000ALICE:1793790000.000200"].author.id == "U000DON"


def test_window_comes_from_the_manifest(inbox_fixture, tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(inbox_fixture, raw)
    manifest = json.loads((raw / "manifest.json").read_text())
    manifest["oldest"] = "1793790000.000100"  # 11:00:00 exactly: inclusive
    (raw / "manifest.json").write_text(json.dumps(manifest))

    ids = [m.id for m in normalize_dir(raw)]

    assert ids[0] == "G000MPIM:1793790000.000100"
    assert "D000ALICE:1793786400.000100" not in ids


def test_missing_whoami_is_an_explicit_error(inbox_fixture, tmp_path):
    raw = tmp_path / "raw"
    shutil.copytree(inbox_fixture, raw)
    (raw / "whoami.json").unlink()

    with pytest.raises(NormalizeError, match="whoami.json"):
        normalize_dir(raw)


def test_search_only_reply_gets_thread_id_from_its_permalink(tmp_path):
    raw = tmp_path / "raw"
    (raw / "search.messages" / "query").mkdir(parents=True)
    (raw / "whoami.json").write_text(
        json.dumps({"ok": True, "url": "https://example.slack.com/", "user_id": "U000DON"})
    )
    match = {
        "channel": {"id": "C000GENERAL", "name": "general", "is_mpim": False},
        "user": "U000ALICE",
        "username": "alice",
        "ts": "1793793700.000100",
        "text": "<@U000DON|don> ping",
        "permalink": "https://example.slack.com/archives/C000GENERAL/p1793793700000100"
        "?thread_ts=1793793600.000100&cid=C000GENERAL",
    }
    (raw / "search.messages" / "query" / "page-001.json").write_text(
        json.dumps({"ok": True, "messages": {"matches": [match]}})
    )

    (msg,) = normalize_dir(raw)

    assert msg.thread_id == "C000GENERAL:1793793600.000100"
    assert msg.author.name == "alice"  # no users.info: falls back to the match's username
    assert msg.text == "@don ping"  # no users.info: falls back to the mention's label
    assert msg.is_mention is True
    assert msg.awaiting_reply is True
