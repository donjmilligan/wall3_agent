from __future__ import annotations

import json
from datetime import timedelta

import pytest
from conftest import FakeResponse, FakeSession, make_client

from wall3_slack import cli


@pytest.fixture
def fake(monkeypatch) -> FakeSession:
    """Install a fake Slack behind cli.make_client; return its session for assertions."""
    client, session, _ = make_client(
        {
            "auth.test": [
                FakeResponse(
                    {
                        "ok": True,
                        "url": "https://example.slack.com/",
                        "team": "example",
                        "user": "don",
                        "user_id": "U000DON",
                    }
                )
            ],
            "users.info": [
                FakeResponse(
                    {
                        "ok": True,
                        "user": {
                            "id": "U000DON",
                            "name": "don",
                            "profile": {"display_name": "Don Example"},
                        },
                    }
                )
            ],
            "chat.postMessage": [
                FakeResponse({"ok": True, "channel": "D000ALICE", "ts": "1793800000.000100"})
            ],
            "chat.getPermalink": [
                FakeResponse(
                    {
                        "ok": True,
                        "channel": "D000ALICE",
                        "permalink": "https://example.slack.com/archives/D000ALICE/p1793800000000100",
                    }
                )
            ],
        }
    )
    monkeypatch.setattr(cli, "make_client", lambda: client)
    return session


@pytest.fixture
def draft(tmp_path):
    path = tmp_path / "draft.txt"
    path.write_text("Reviewed, looks good.\nShip it.\n")
    return path


# ------------------------------------------------------------------- send


def test_send_without_confirm_previews_and_exits_3_with_zero_network(fake, draft, capsys):
    code = cli.main(
        [
            "send",
            "--channel",
            "D000ALICE",
            "--thread-ts",
            "1793786400.000100",
            "--text-file",
            str(draft),
        ]
    )

    out = capsys.readouterr().out
    assert code == 3
    assert fake.calls == []  # not even auth.test
    assert "DRY RUN" in out
    assert "D000ALICE" in out
    assert "1793786400.000100" in out
    assert "Reviewed, looks good.\nShip it.\n" in out


def test_send_with_confirm_posts_exactly_once_and_prints_permalink(fake, draft, capsys):
    code = cli.main(
        [
            "send",
            "--channel",
            "D000ALICE",
            "--thread-ts",
            "1793786400.000100",
            "--text-file",
            str(draft),
            "--confirm",
        ]
    )

    assert code == 0
    posts = fake.calls_to("chat.postMessage")
    assert len(posts) == 1
    assert posts[0]["json"] == {
        "channel": "D000ALICE",
        "text": "Reviewed, looks good.\nShip it.\n",
        "thread_ts": "1793786400.000100",
    }
    assert capsys.readouterr().out.strip() == (
        "https://example.slack.com/archives/D000ALICE/p1793800000000100"
    )


def test_send_permalink_failure_after_post_still_exits_0(fake, draft, capsys, monkeypatch):
    """The post landed; a non-zero exit would invite a duplicate re-send."""
    fake.routes["chat.getPermalink"] = [FakeResponse({"ok": False, "error": "message_not_found"})]

    code = cli.main(["send", "--channel", "D000ALICE", "--text-file", str(draft), "--confirm"])

    assert code == 0
    assert len(fake.calls_to("chat.postMessage")) == 1
    assert "posted to D000ALICE at ts 1793800000.000100" in capsys.readouterr().out


def test_send_empty_text_file_is_an_error_with_no_post(fake, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n")

    assert cli.main(["send", "--channel", "D1", "--text-file", str(empty), "--confirm"]) == 1
    assert fake.calls == []


def test_send_missing_text_file_is_an_error(fake, tmp_path):
    assert (
        cli.main(["send", "--channel", "D1", "--text-file", str(tmp_path / "nope"), "--confirm"])
        == 1
    )
    assert fake.calls == []


def test_send_confirm_cannot_be_abbreviated(fake, draft):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["send", "--channel", "D1", "--text-file", str(draft), "--conf"])
    assert excinfo.value.code == 2
    assert fake.calls == []


def test_send_has_no_inline_text_flag():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["send", "--channel", "D1", "--text", "hi", "--confirm"])
    assert excinfo.value.code == 2


# --------------------------------------------------------------- whoami


def test_whoami_json(fake, capsys):
    assert cli.main(["whoami", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "source": "slack",
        "id": "U000DON",
        "name": "Don Example",
    }


def test_missing_token_exits_1_with_actionable_message(monkeypatch, tmp_path, capsys):
    from wall3_slack import config as config_module

    monkeypatch.setattr(config_module, "_PACKAGE_ROOT", tmp_path / "pkg")
    monkeypatch.setattr(config_module, "_WORKING_ROOT", tmp_path)
    monkeypatch.setattr(config_module, "_primary_checkout_root", lambda: None)

    assert cli.main(["whoami"]) == 1
    assert "SLACK_USER_TOKEN is not set" in capsys.readouterr().err


def test_api_auth_error_exits_1(monkeypatch, capsys):
    client, _, _ = make_client(
        {"auth.test": [FakeResponse({"ok": False, "error": "invalid_auth"})]}
    )
    monkeypatch.setattr(cli, "make_client", lambda: client)

    assert cli.main(["whoami"]) == 1
    assert "invalid_auth" in capsys.readouterr().err


# ------------------------------------------------------------ normalize


def test_normalize_json_is_pure_jsonl_on_stdout(inbox_fixture, capsys):
    assert cli.main(["normalize", str(inbox_fixture), "--json"]) == 0

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 8
    assert all(json.loads(line)["source"] == "slack" for line in lines)


def test_normalize_human_output_flags_awaiting(inbox_fixture, capsys):
    assert cli.main(["normalize", str(inbox_fixture)]) == 0

    out = capsys.readouterr().out
    assert "dm @Alice Example  Alice Example  [AWAITING REPLY]" in out
    assert "channel #general  Alice Example  [mention, AWAITING REPLY]" in out
    assert "mpim alice, bob, don  Bob Example\n" in out  # replied to: no flag


def test_normalize_missing_dir_exits_1(tmp_path, capsys):
    assert cli.main(["normalize", str(tmp_path / "nope")]) == 1
    assert "not found" in capsys.readouterr().err


# -------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("4h", timedelta(hours=4)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
    ],
)
def test_parse_since(value, expected):
    assert cli.parse_since(value) == expected


@pytest.mark.parametrize("value", ["", "24", "h", "1w", "-1h", "0h", "1.5h"])
def test_parse_since_rejects(value):
    with pytest.raises(cli.argparse.ArgumentTypeError):
        cli.parse_since(value)


def test_bad_since_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["inbox", "--since", "yesterday"])
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("C000GENERAL:1793718000.000100", ("C000GENERAL", "1793718000.000100")),
        (
            "https://example.slack.com/archives/C000GENERAL/p1793718000000100",
            ("C000GENERAL", "1793718000.000100"),
        ),
        (
            "https://example.slack.com/archives/C000GENERAL/p1793797200000100"
            "?thread_ts=1793718000.000100&cid=C000GENERAL",
            ("C000GENERAL", "1793718000.000100"),
        ),
    ],
)
def test_parse_thread_ref(ref, expected):
    assert cli.parse_thread_ref(ref) == expected


def test_parse_thread_ref_rejects_garbage():
    with pytest.raises(ValueError):
        cli.parse_thread_ref("not a link")


# ------------------------------------------------------- network verbs


def test_thread_verb_fetches_replies_and_prints_jsonl(monkeypatch, tmp_path, capsys):
    client, session, _ = make_client(
        {
            "auth.test": [
                FakeResponse(
                    {"ok": True, "url": "https://example.slack.com/", "user_id": "U000DON"}
                )
            ],
            "conversations.info": [
                FakeResponse({"ok": True, "channel": {"id": "C000GENERAL", "name": "general"}})
            ],
            "conversations.replies": [
                FakeResponse(
                    {
                        "ok": True,
                        "messages": [
                            {
                                "user": "U000DON",
                                "text": "parent",
                                "ts": "1793718000.000100",
                                "thread_ts": "1793718000.000100",
                            },
                            {
                                "user": "U000BOB",
                                "text": "<@U000DON> reply",
                                "ts": "1793797200.000100",
                                "thread_ts": "1793718000.000100",
                            },
                        ],
                    }
                )
            ],
            "users.info": [FakeResponse({"ok": False, "error": "user_not_found"})],
        }
    )
    monkeypatch.setattr(cli, "make_client", lambda: client)
    monkeypatch.setattr(cli, "raw_root", lambda: tmp_path / "raw")

    assert cli.main(["thread", "C000GENERAL:1793718000.000100", "--json"]) == 0

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in records] == [
        "C000GENERAL:1793718000.000100",
        "C000GENERAL:1793797200.000100",
    ]
    assert records[1]["container"]["name"] == "#general"
    assert records[1]["is_mention"] is True
    assert records[1]["awaiting_reply"] is True
    (replies,) = session.calls_to("conversations.replies")
    assert replies["params"]["ts"] == "1793718000.000100"


def test_channel_verb_resolves_a_name(monkeypatch, tmp_path, capsys):
    client, session, _ = make_client(
        {
            "auth.test": [
                FakeResponse(
                    {"ok": True, "url": "https://example.slack.com/", "user_id": "U000DON"}
                )
            ],
            "conversations.list": [
                FakeResponse(
                    {
                        "ok": True,
                        "channels": [{"id": "C000GENERAL", "name": "general"}],
                        "response_metadata": {"next_cursor": ""},
                    }
                )
            ],
            "conversations.info": [
                FakeResponse({"ok": True, "channel": {"id": "C000GENERAL", "name": "general"}})
            ],
            "conversations.history": [
                FakeResponse(
                    {
                        "ok": True,
                        "messages": [
                            {"user": "U000BOB", "text": "two", "ts": "1793797200.000100"},
                            {"user": "U000BOB", "text": "one", "ts": "1793790000.000100"},
                        ],
                    }
                )
            ],
            "users.info": [FakeResponse({"ok": False, "error": "user_not_found"})],
        }
    )
    monkeypatch.setattr(cli, "make_client", lambda: client)
    monkeypatch.setattr(cli, "raw_root", lambda: tmp_path / "raw")

    assert cli.main(["channel", "#general", "--limit", "1", "--json"]) == 0

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["text"] for r in records] == ["two"]  # newest `limit` kept
    (history,) = session.calls_to("conversations.history")
    assert history["params"]["channel"] == "C000GENERAL"
    assert history["params"]["limit"] == 1
    assert session.calls_to("conversations.list")[0]["params"]["types"] == (
        "public_channel,private_channel"
    )


def test_channel_verb_unknown_name_exits_1(monkeypatch, tmp_path, capsys):
    client, _, _ = make_client(
        {
            "auth.test": [
                FakeResponse(
                    {"ok": True, "url": "https://example.slack.com/", "user_id": "U000DON"}
                )
            ],
            "conversations.list": [FakeResponse({"ok": True, "channels": []})],
        }
    )
    monkeypatch.setattr(cli, "make_client", lambda: client)
    monkeypatch.setattr(cli, "raw_root", lambda: tmp_path / "raw")

    assert cli.main(["channel", "nope"]) == 1
    assert "no channel named 'nope'" in capsys.readouterr().err


def test_user_verb_by_name(monkeypatch, capsys):
    client, session, _ = make_client(
        {
            "users.list": [
                FakeResponse(
                    {
                        "ok": True,
                        "members": [
                            {
                                "id": "U000BOB",
                                "name": "bob",
                                "profile": {"display_name": "Bob Example"},
                            },
                            {
                                "id": "U000ALICE",
                                "name": "alice",
                                "real_name": "Alice Example",
                                "profile": {"display_name": "Alice Example"},
                                "tz": "UTC",
                            },
                        ],
                    }
                )
            ],
        }
    )
    monkeypatch.setattr(cli, "make_client", lambda: client)

    assert cli.main(["user", "@alice"]) == 0

    out = capsys.readouterr().out
    assert "U000ALICE" in out
    assert "Alice Example" in out
    assert "U000BOB" not in out


def test_search_verb(monkeypatch, tmp_path, capsys):
    client, session, _ = make_client(
        {
            "auth.test": [
                FakeResponse(
                    {"ok": True, "url": "https://example.slack.com/", "user_id": "U000DON"}
                )
            ],
            "search.messages": [
                FakeResponse(
                    {
                        "ok": True,
                        "messages": {
                            "matches": [
                                {
                                    "channel": {"id": "C000GENERAL", "name": "general"},
                                    "user": "U000BOB",
                                    "username": "bob",
                                    "ts": "1793797200.000100",
                                    "text": "deploy plan",
                                    "permalink": "https://example.slack.com/archives/C000GENERAL/p1793797200000100",
                                }
                            ],
                            "paging": {"pages": 1},
                        },
                    }
                )
            ],
            "users.info": [FakeResponse({"ok": False, "error": "user_not_found"})],
        }
    )
    monkeypatch.setattr(cli, "make_client", lambda: client)
    monkeypatch.setattr(cli, "raw_root", lambda: tmp_path / "raw")

    assert cli.main(["search", "deploy plan", "--json"]) == 0

    (record,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert record["container"]["name"] == "#general"
    assert record["is_mention"] is False
    assert session.calls_to("search.messages")[0]["params"]["query"] == "deploy plan"
