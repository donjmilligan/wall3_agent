from __future__ import annotations

import json
import os
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

#: sha256 of the `draft` fixture's bytes, computed once by hand (hashlib) and pinned.
DRAFT_SHA = "61ccb2e5ab5b59be52c66b7d0a5f6c4dd503fc5808695a8d9017071677e9de77"
OTHER_SHA = "0" * 64


def send_argv(draft, *extra: str) -> list[str]:
    return [
        "send",
        "--channel",
        "D000ALICE",
        "--thread-ts",
        "1793786400.000100",
        "--text-file",
        str(draft),
        *extra,
    ]


def test_send_without_confirm_previews_and_exits_3_with_zero_network(fake, draft, capsys):
    code = cli.main(send_argv(draft))

    out = capsys.readouterr().out
    assert code == 3
    assert fake.calls == []  # not even auth.test
    assert "DRY RUN" in out
    assert "D000ALICE" in out
    assert "1793786400.000100" in out
    assert "Reviewed, looks good.\nShip it.\n" in out
    assert f"sha256: {DRAFT_SHA}" in out.splitlines()
    assert f"--confirm --expect-sha256 {DRAFT_SHA}" in out


def test_send_dry_run_reports_whether_expected_hash_matches(fake, draft, capsys):
    assert cli.main(send_argv(draft, "--expect-sha256", DRAFT_SHA)) == 3
    assert "--expect-sha256: matches" in capsys.readouterr().out
    assert cli.main(send_argv(draft, "--expect-sha256", OTHER_SHA)) == 3
    assert "hash mismatch" in capsys.readouterr().out
    assert fake.calls == []


def test_send_with_confirm_and_matching_hash_posts_exactly_once(fake, draft, capsys):
    code = cli.main(send_argv(draft, "--confirm", "--expect-sha256", DRAFT_SHA))

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


def test_send_hash_is_case_insensitive(fake, draft):
    assert cli.main(send_argv(draft, "--confirm", "--expect-sha256", DRAFT_SHA.upper())) == 0
    assert len(fake.calls_to("chat.postMessage")) == 1


def test_send_confirm_without_expected_hash_exits_4_with_zero_network(fake, draft, capsys):
    code = cli.main(send_argv(draft, "--confirm"))

    assert code == 4
    assert fake.calls == []
    assert "--confirm requires --expect-sha256" in capsys.readouterr().err


@pytest.mark.parametrize("expected", [OTHER_SHA, "abc", DRAFT_SHA[:-1] + "0"])
def test_send_confirm_with_wrong_hash_exits_4_with_zero_network(fake, draft, capsys, expected):
    code = cli.main(send_argv(draft, "--confirm", "--expect-sha256", expected))

    assert code == 4
    assert fake.calls == []
    assert "--expect-sha256" in capsys.readouterr().err


def test_send_detects_text_changed_after_preview(fake, draft, capsys):
    """The TOCTOU case: previewed text A, file swapped to B, confirmed with A's hash."""
    assert cli.main(send_argv(draft)) == 3
    assert f"sha256: {DRAFT_SHA}" in capsys.readouterr().out
    draft.write_text("Something else entirely.\n")

    assert cli.main(send_argv(draft, "--confirm", "--expect-sha256", DRAFT_SHA)) == 4
    assert fake.calls == []
    assert "hash mismatch" in capsys.readouterr().err


def test_send_posts_the_bytes_it_hashed_without_rereading(fake, draft, monkeypatch):
    """The file is read once: a change after the hash check cannot reach Slack."""
    real_make_client = cli.make_client

    def make_client_after_swap():
        draft.write_text("Swapped after the check.\n")
        return real_make_client()

    monkeypatch.setattr(cli, "make_client", make_client_after_swap)

    assert cli.main(send_argv(draft, "--confirm", "--expect-sha256", DRAFT_SHA)) == 0
    posts = fake.calls_to("chat.postMessage")
    assert [p["json"]["text"] for p in posts] == ["Reviewed, looks good.\nShip it.\n"]


def test_send_permalink_failure_after_post_still_exits_0(fake, draft, capsys, monkeypatch):
    """The post landed; a non-zero exit would invite a duplicate re-send."""
    fake.routes["chat.getPermalink"] = [FakeResponse({"ok": False, "error": "message_not_found"})]

    code = cli.main(send_argv(draft, "--confirm", "--expect-sha256", DRAFT_SHA))

    assert code == 0
    assert len(fake.calls_to("chat.postMessage")) == 1
    assert "posted to D000ALICE at ts 1793800000.000100" in capsys.readouterr().out


def test_send_empty_text_file_is_an_error_with_no_post(fake, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n")

    assert cli.main(["send", "--channel", "D1", "--text-file", str(empty), "--confirm"]) == 1
    assert fake.calls == []


def test_send_missing_text_file_is_an_error(fake, tmp_path, capsys):
    assert cli.main(["send", "--channel", "D1", "--text-file", str(tmp_path / "nope")]) == 1
    assert "cannot open" in capsys.readouterr().err
    assert fake.calls == []


def test_send_refuses_a_fifo_without_blocking(fake, tmp_path, capsys):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)  # no writer: a blocking open() would hang this test

    code = cli.main(["send", "--channel", "D1", "--text-file", str(fifo), "--confirm"])

    assert code == 1
    assert "not a regular file" in capsys.readouterr().err
    assert fake.calls == []


def test_send_refuses_a_directory_and_a_symlink_to_one(fake, tmp_path, capsys):
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    for target in (tmp_path, link):
        assert cli.main(["send", "--channel", "D1", "--text-file", str(target)]) == 1
        assert "not a regular file" in capsys.readouterr().err
    assert fake.calls == []


def test_send_follows_a_symlink_to_a_regular_file(fake, draft, tmp_path, capsys):
    link = tmp_path / "link.txt"
    link.symlink_to(draft)
    assert cli.main(["send", "--channel", "D1", "--text-file", str(link)]) == 3
    assert f"sha256: {DRAFT_SHA}" in capsys.readouterr().out


def test_send_refuses_non_utf8(fake, tmp_path, capsys):
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe hi")
    assert cli.main(["send", "--channel", "D1", "--text-file", str(bad)]) == 1
    assert "not UTF-8" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [
        ["--channel", "C0OTHER"],
        ["--text-file", "other.txt"],
        ["--thread-ts", "1.2"],
        ["--confirm", "--confirm"],
        ["--expect-sha256", DRAFT_SHA, "--expect-sha256", DRAFT_SHA],
    ],
)
def test_send_rejects_any_repeated_option(fake, draft, capsys, extra):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(send_argv(draft, *extra))
    assert excinfo.value.code == 2
    assert "given more than once" in capsys.readouterr().err
    assert fake.calls == []


def test_send_single_options_still_parse(fake, draft):
    """Positive control for the repeat check: one of each is accepted."""
    assert cli.main(send_argv(draft, "--confirm", "--expect-sha256", DRAFT_SHA)) == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["send", "--channel", "D1", "--text-file", "x", "--conf"],
        ["send", "--channel", "D1", "--tex", "x"],
        ["send", "--chan", "D1", "--text-file", "x"],
        ["send", "--channel", "D1", "--text-file", "x", "--confirm", "--expect", DRAFT_SHA],
    ],
)
def test_send_options_cannot_be_abbreviated(fake, argv):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)
    assert excinfo.value.code == 2
    assert fake.calls == []


def test_read_options_cannot_be_repeated_or_abbreviated():
    for argv in (["inbox", "--since", "1h", "--since", "2h"], ["inbox", "--sin", "1h"]):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == 2


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
