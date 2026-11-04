"""Ids become path components under the raw run dir; unsafe ones are refused, not sanitised."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import FakeResponse, make_client

from wall3_slack import cli
from wall3_slack.extract import extract_thread
from wall3_slack.extract.common import RunRecord, fetch_users
from wall3_slack.extract.store import RawStore, UnsafeComponentError, safe_component

NOW = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["C0TEST", "D0TEST", "U0TEST", "1793786400.000100", "a_b-c:d"])
def test_safe_component_accepts_api_ids(value):
    assert safe_component(value) == value


@pytest.mark.parametrize("value", ["..", ".", "../x", "a/b", "C1/../../etc", "", "a b", "x\n"])
def test_safe_component_refuses_path_like_values(value):
    with pytest.raises(UnsafeComponentError):
        safe_component(value)


@pytest.mark.parametrize(("channel", "ts"), [("../../evil", "1.2"), ("C0TEST", "../1")])
def test_thread_extract_refuses_unsafe_ids_before_any_call(tmp_path, channel, ts):
    client, session, _ = make_client({})
    with pytest.raises(UnsafeComponentError):
        extract_thread(client, tmp_path / "run", channel=channel, ts=ts, now=NOW)
    assert session.calls == []
    assert not (tmp_path / "evil").exists()


def test_thread_extract_with_safe_ids_writes_under_the_run_dir(tmp_path):
    """Positive control for the refusal above: the same call with safe ids writes files."""
    client, _, _ = make_client(
        {
            "auth.test": [FakeResponse({"ok": True, "user_id": "U0DON", "user": "don"})],
            "users.info": [FakeResponse({"ok": True, "user": {"id": "U0DON"}})],
            "conversations.info": [FakeResponse({"ok": True, "channel": {"id": "C0TEST"}})],
            "conversations.replies": [FakeResponse({"ok": True, "messages": []})],
        }
    )
    out = extract_thread(client, tmp_path / "run", channel="C0TEST", ts="1.2", now=NOW)
    assert (out / "conversations.info" / "C0TEST.json").is_file()
    assert (out / "conversations.replies" / "C0TEST-1.2" / "page-001.json").is_file()


def test_unsafe_user_id_is_skipped_with_a_note_not_written(tmp_path):
    client, session, _ = make_client(
        {"users.info": [FakeResponse({"ok": True, "user": {"id": "U0OK"}})]}
    )
    run = RunRecord(verb="test", fetched_at="x")
    fetch_users(client, RawStore(tmp_path), run, ["../../evil", "U0OK"], max_users=10)
    assert (tmp_path / "users.info" / "U0OK.json").is_file()
    assert [c["params"] for c in session.calls] == [{"user": "U0OK"}]
    assert any("not a valid user id" in note for note in run.skipped)


def test_cli_reports_unsafe_id_as_exit_1(monkeypatch, tmp_path, capsys):
    client, session, _ = make_client({})
    monkeypatch.setattr(cli, "make_client", lambda: client)
    monkeypatch.setattr(cli, "raw_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "parse_thread_ref", lambda ref: ("..", "1.2"))
    assert cli.main(["thread", "C1:1.2"]) == 1
    assert "path component" in capsys.readouterr().err
    assert session.calls == []
