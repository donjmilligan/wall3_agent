"""Normalize the synthetic inbox fixture; every expected value is written by hand.

The fixture (tests/fixtures/raw/inbox) was authored from chosen ISO times, so
the timestamps below are the values the fixture was *designed* around, not
values read back out of the code under test.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.conftest import INBOX_RAW
from wall3_clickup.normalize import NormalizeError, normalize_raw_dir


@pytest.fixture(scope="module")
def by_id() -> dict[str, dict]:
    result = normalize_raw_dir(INBOX_RAW)
    return {m.id: m.to_dict() for m in result.messages}


def test_exact_set_of_messages_emitted(by_id):
    # Excluded, each for a different reason:
    #   5100 -- on my assigned task, but before the manifest's since window
    #   5301 -- on a task I'm not assigned to/watching/creator of, no mention
    #   m-101 -- public-channel chatter, no mention
    #   7778 -- reply whose parent comment is not in the snapshot
    assert set(by_id) == {
        "5101", "5001", "5002", "5401", "5201", "5202",
        "m-100", "m-102", "m-199", "m-200",
    }  # fmt: skip


def test_output_is_sorted_by_time():
    ids = [m.id for m in normalize_raw_dir(INBOX_RAW).messages]
    assert ids == [
        "5101", "5001", "5002", "5401", "5201", "5202", "m-100", "m-102", "m-199", "m-200",
    ]  # fmt: skip


def test_comment_with_tag_mention_awaiting_my_reply(by_id):
    assert by_id["5201"] == {
        "source": "clickup",
        "id": "5201",
        "thread_id": "ghi789",
        "container": {"kind": "task", "id": "ghi789", "name": "Update onboarding docs"},
        "author": {"id": "1002", "name": "Alice Example"},
        "ts": "2026-11-04T12:15:00Z",
        "text": "@Don Example the onboarding checklist needs your sign-off.",
        "permalink": "https://app.clickup.com/t/ghi789?comment=5201",
        "is_mention": True,
        "is_dm": False,
        # Bob replied after Alice (5202), but Bob is not me -> still awaiting.
        "awaiting_reply": True,
    }


def test_threaded_reply_shares_the_task_thread(by_id):
    reply = by_id["5202"]
    assert reply["thread_id"] == "ghi789"
    assert reply["ts"] == "2026-11-04T12:20:00Z"
    assert reply["author"] == {"id": "1003", "name": "Bob Example"}
    assert (reply["is_mention"], reply["awaiting_reply"]) == (False, False)


def test_text_mention_that_i_already_answered(by_id):
    mention = by_id["5001"]
    assert mention["text"] == "@Don Example can you approve the report numbers?"
    assert mention["is_mention"] is True  # plain-text @name, no tag item
    assert mention["awaiting_reply"] is False  # my 5002 came after it

    mine = by_id["5002"]
    assert mine["author"] == {"id": "1001", "name": "Don Example"}
    assert (mine["is_mention"], mine["awaiting_reply"]) == (False, False)
    assert mine["ts"] == "2026-11-04T10:00:00Z"


def test_non_mention_on_my_assigned_task(by_id):
    m = by_id["5101"]
    # A tag item is present, but it tags Alice, not me.
    assert m["text"] == "Pinging @Alice Example about the deploy window."
    assert m["container"] == {"kind": "task", "id": "abc123", "name": "Fix login redirect"}
    assert (m["is_mention"], m["is_dm"], m["awaiting_reply"]) == (False, False, False)


def test_structured_tag_is_a_mention_even_when_the_rendered_name_differs(raw_copy):
    """A tag item carrying my id but another display name (e.g. a renamed account)
    is still a mention -- the text fallback alone would miss it."""
    page = raw_copy / "comments" / "ghi789" / "page-000.json"
    data = json.loads(page.read_text(encoding="utf-8"))
    data["comments"][0]["comment"][0]["user"] = {"id": 1001, "username": "don.e"}
    _write(page, data)

    by_id = {m.id: m for m in normalize_raw_dir(raw_copy).messages}

    assert by_id["5201"].text.startswith("@don.e ")
    assert (by_id["5201"].is_mention, by_id["5201"].awaiting_reply) == (True, True)


def test_orphan_task_falls_back_to_task_id_and_renders_bare_tag(by_id):
    m = by_id["5401"]
    assert m["container"] == {"kind": "task", "id": "zzz999", "name": "zzz999"}
    # The tag item carries only a user id; the name comes from teams.json.
    assert m["text"] == "Heads up @Don Example, this one is yours."
    assert (m["is_mention"], m["awaiting_reply"]) == (True, True)


def test_orphan_reply_is_skipped_with_a_warning():
    result = normalize_raw_dir(INBOX_RAW)
    assert "7778" not in {m.id for m in result.messages}
    assert any("replies/7777.json" in w for w in result.warnings)


def test_chat_mention_via_tagged_users_answered_in_thread(by_id):
    assert by_id["m-100"] == {
        "source": "clickup",
        "id": "m-100",
        "thread_id": "m-100",
        "container": {"kind": "chat", "id": "ch-general", "name": "general"},
        "author": {"id": "1002", "name": "Alice Example"},
        "ts": "2026-11-04T13:00:00Z",
        # Content says only "@Don" -- the mention comes from tagged_users.
        "text": "@Don can you review the release notes?",
        "permalink": "https://app.clickup.com/9000001/chat/r/ch-general",
        "is_mention": True,
        "is_dm": False,
        "awaiting_reply": False,  # my reply m-102 is the latest in the thread
    }
    assert by_id["m-102"]["thread_id"] == "m-100"
    assert by_id["m-102"]["author"]["id"] == "1001"


def test_dm_awaiting_reply_and_my_own_dm(by_id):
    theirs = by_id["m-200"]
    assert theirs["container"] == {"kind": "dm", "id": "dm-alice", "name": "Alice Example"}
    assert theirs["thread_id"] is None
    assert theirs["ts"] == "2026-11-04T14:00:00Z"
    assert (theirs["is_dm"], theirs["is_mention"], theirs["awaiting_reply"]) == (True, False, True)

    mine = by_id["m-199"]
    assert (mine["is_dm"], mine["awaiting_reply"]) == (True, False)


# ---------------------------------------------------------------- edge cases on copies


@pytest.fixture
def raw_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "raw"
    shutil.copytree(INBOX_RAW, dest)
    return dest


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_my_reply_ends_awaiting(raw_copy):
    """Flip the ghi789 thread: once my reply is the newest, Alice's mention is answered."""
    _write(
        raw_copy / "replies" / "5201.json",
        {
            "comments": [
                {
                    "id": "5203",
                    "comment_text": "Signed off.",
                    "user": {"id": 1001, "username": "Don Example"},
                    "date": "1793796000000",  # 2026-11-04T12:40:00Z
                }
            ]
        },
    )
    by_id = {m.id: m for m in normalize_raw_dir(raw_copy).messages}
    assert by_id["5201"].awaiting_reply is False
    assert by_id["5203"].ts == "2026-11-04T12:40:00Z"


def test_mentioning_myself_is_not_a_mention(raw_copy):
    page = raw_copy / "comments" / "def456" / "page-000.json"
    data = json.loads(page.read_text(encoding="utf-8"))
    data["comments"][0]["comment"] = [{"text": "@Don Example note to self"}]
    _write(page, data)

    by_id = {m.id: m for m in normalize_raw_dir(raw_copy).messages}

    assert by_id["5002"].text == "@Don Example note to self"
    assert (by_id["5002"].is_mention, by_id["5002"].awaiting_reply) == (False, False)


def test_task_snapshot_is_emitted_whole(raw_copy):
    """`task`/`chat` snapshots skip the inbox relevance + window filter."""
    _write(raw_copy / "manifest.json", {"kind": "task", "team_id": "9000001", "since_ms": None})
    ids = {m.id for m in normalize_raw_dir(raw_copy).messages}
    assert {"5100", "5301", "m-101"} <= ids
    assert "5201" in ids


def test_without_tagged_users_a_bare_first_name_is_not_a_mention(raw_copy):
    (raw_copy / "chat" / "tagged_users" / "m-100.json").unlink()
    by_id = {m.id: m for m in normalize_raw_dir(raw_copy).messages}
    # "@Don" alone is not my full name; without tagged_users it cannot be trusted.
    assert "m-100" not in by_id
    assert "m-200" in by_id


def test_missing_whoami_is_an_error_not_a_guess(raw_copy):
    (raw_copy / "whoami.json").unlink()
    with pytest.raises(NormalizeError, match="whoami.json"):
        normalize_raw_dir(raw_copy)


def test_not_a_directory_is_an_error(tmp_path):
    with pytest.raises(NormalizeError, match="not a directory"):
        normalize_raw_dir(tmp_path / "missing")
