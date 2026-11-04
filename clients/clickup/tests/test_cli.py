from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeResponse, FakeSession
from wall3_clickup.cli import EXIT_ERROR, EXIT_OK, EXIT_REFUSED, main, parse_since

V2 = "https://api.clickup.com/api/v2"
V3 = "https://api.clickup.com/api/v3"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_test_token")
    monkeypatch.setenv("CLICKUP_TEAM_ID", "9000001")


@pytest.fixture
def text_file(tmp_path: Path) -> Path:
    path = tmp_path / "reply.txt"
    # Shell metacharacters must arrive literally -- text never passes through a shell.
    path.write_text("Thanks Alice -- merged.\nCost was $(echo 5) & `none`.\n", encoding="utf-8")
    return path


EXPECTED_TEXT = "Thanks Alice -- merged.\nCost was $(echo 5) & `none`."


# ---------------------------------------------------------------- write refusal (exit 3)


def test_comment_without_confirm_previews_and_makes_no_request(creds, text_file, capsys):
    session = FakeSession([])

    code = main(["comment", "abc123", "--text-file", str(text_file)], session=session)

    assert code == EXIT_REFUSED
    assert session.calls == []
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "task abc123" in out
    assert EXPECTED_TEXT in out


def test_comment_with_confirm_posts_exactly_once(creds, text_file, capsys):
    session = FakeSession([FakeResponse(200, {"id": "458", "hist_id": "1", "date": 1})])

    code = main(
        ["comment", "abc123", "--text-file", str(text_file), "--confirm", "--json"],
        session=session,
    )

    assert code == EXIT_OK
    assert [(c["method"], c["url"]) for c in session.calls] == [
        ("POST", f"{V2}/task/abc123/comment")
    ]
    assert session.calls[0]["json"] == {"comment_text": EXPECTED_TEXT, "notify_all": False}
    assert json.loads(capsys.readouterr().out) == {
        "task_id": "abc123",
        "comment_id": "458",
        "permalink": "https://app.clickup.com/t/abc123?comment=458",
    }


def test_comment_preview_needs_no_token(text_file, capsys):
    """Previewing is offline: it works even before credentials are set up."""
    session = FakeSession([])

    assert main(["comment", "abc123", "--text-file", str(text_file)], session=session) == 3
    assert session.calls == []
    assert EXPECTED_TEXT in capsys.readouterr().out


def test_status_without_confirm_previews_both_steps_and_makes_no_request(creds, text_file, capsys):
    session = FakeSession([])

    code = main(
        ["status", "abc123", "in review", "--comment-file", str(text_file)], session=session
    )

    assert code == EXIT_REFUSED
    assert session.calls == []
    out = capsys.readouterr().out
    assert EXPECTED_TEXT in out
    assert "'in review'" in out
    assert out.index("POST comment") < out.index("PUT status")


def test_status_with_confirm_comments_then_sets_status(creds, text_file):
    session = FakeSession([FakeResponse(200, {"id": "459"}), FakeResponse(200, {"id": "abc123"})])

    code = main(
        ["status", "abc123", "in review", "--comment-file", str(text_file), "--confirm"],
        session=session,
    )

    assert code == EXIT_OK
    assert [(c["method"], c["url"]) for c in session.calls] == [
        ("POST", f"{V2}/task/abc123/comment"),
        ("PUT", f"{V2}/task/abc123"),
    ]
    assert session.calls[0]["json"]["comment_text"] == EXPECTED_TEXT
    assert session.calls[1]["json"] == {"status": "in review"}


def test_status_failure_after_comment_is_reported(creds, text_file, capsys):
    session = FakeSession(
        [FakeResponse(200, {"id": "459"}), FakeResponse(400, {"err": "Status not found"})]
    )

    code = main(
        ["status", "abc123", "nope", "--comment-file", str(text_file), "--confirm"],
        session=session,
    )

    assert code == EXIT_ERROR
    assert session.methods == ["POST", "PUT"]
    err = capsys.readouterr().err
    assert "459" in err and "status change failed" in err


def test_empty_text_file_is_refused_before_any_request(creds, tmp_path, capsys):
    empty = tmp_path / "empty.txt"
    empty.write_text("\n\n")
    session = FakeSession([])

    code = main(["comment", "abc123", "--text-file", str(empty), "--confirm"], session=session)

    assert code == EXIT_ERROR
    assert session.calls == []
    assert "empty" in capsys.readouterr().err


def test_status_requires_a_comment_file():
    with pytest.raises(SystemExit) as excinfo:
        main(["status", "abc123", "done", "--confirm"])
    assert excinfo.value.code == 2


def test_confirmed_write_without_token_fails_cleanly(text_file, capsys):
    session = FakeSession([])

    code = main(["comment", "abc123", "--text-file", str(text_file), "--confirm"], session=session)

    assert code == EXIT_ERROR
    assert session.calls == []
    assert "CLICKUP_API_TOKEN is not set" in capsys.readouterr().err


def test_path_like_task_id_is_rejected(creds, text_file):
    session = FakeSession([])
    assert main(["comment", "../x", "--text-file", str(text_file)], session=session) == 1
    assert session.calls == []


# ---------------------------------------------------------------- --since


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("4h", timedelta(hours=4)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
    ],
)
def test_parse_since_accepts_documented_forms(raw, expected):
    assert parse_since(raw) == expected


@pytest.mark.parametrize("raw", ["1w", "0h", "h", "24", "-4h", "4 h"])
def test_parse_since_rejects_other_forms(raw):
    with pytest.raises(SystemExit) as excinfo:
        main(["inbox", "--since", raw])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------- reads

NOW = 1_793_808_000.0  # 2026-11-04T16:00:00Z, chosen by hand


def _router(routes: Mapping[tuple[str, str], Any]):
    def route(method, url, params):  # noqa: ANN001
        key = (method, url)
        if key not in routes:
            return FakeResponse(404, {"err": f"no route for {method} {url}"})
        return FakeResponse(200, routes[key])

    return route


ME = {"user": {"id": 1001, "username": "Don Example"}}
TEAMS = {"teams": [{"id": "9000001", "name": "Example Workspace", "members": []}]}


def test_inbox_end_to_end_is_read_only_and_uses_the_window(creds, tmp_path, capsys):
    routes = {
        ("GET", f"{V2}/user"): ME,
        ("GET", f"{V2}/team"): TEAMS,
        ("GET", f"{V2}/team/9000001/task"): {
            "tasks": [
                {
                    "id": "abc123",
                    "name": "Fix login redirect",
                    "date_updated": "1793800000000",
                    "assignees": [{"id": 1001}],
                }
            ]
        },
        ("GET", f"{V2}/task/abc123/comment"): {
            "comments": [
                {
                    "id": "1",
                    "comment_text": "@Don Example please look",
                    "user": {"id": 1002, "username": "Alice Example"},
                    "date": "1793800000000",
                    "reply_count": "0",
                }
            ]
        },
        ("GET", f"{V3}/workspaces/9000001/chat/channels"): {"next_cursor": "", "data": []},
    }
    session = FakeSession(router=_router(routes))

    code = main(
        ["inbox", "--since", "4h", "--json"], session=session, raw_root=tmp_path, now=lambda: NOW
    )

    assert code == EXIT_OK
    assert not WRITE_METHODS & set(session.methods)
    task_call = next(c for c in session.calls if c["url"].endswith("/team/9000001/task"))
    assert task_call["params"]["date_updated_gt"] == 1_793_793_600_000  # NOW - 4h, in ms
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["1"]
    assert json.loads(lines[0])["awaiting_reply"] is True
    snapshots = list(tmp_path.iterdir())
    assert len(snapshots) == 1
    assert (snapshots[0] / "whoami.json").is_file()


def test_inbox_without_team_id_lists_the_teams(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_test_token")
    session = FakeSession([FakeResponse(200, TEAMS)])

    code = main(["inbox"], session=session, raw_root=tmp_path)

    assert code == EXIT_ERROR
    err = capsys.readouterr().err
    assert "CLICKUP_TEAM_ID=9000001" in err
    assert "Example Workspace" in err


def test_whoami_without_team_id_tells_which_to_set(monkeypatch, capsys):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_test_token")
    session = FakeSession([FakeResponse(200, ME), FakeResponse(200, TEAMS)])

    assert main(["whoami"], session=session) == EXIT_OK

    captured = capsys.readouterr()
    assert "Don Example (id 1001)" in captured.out
    assert "CLICKUP_TEAM_ID=9000001" in captured.err


def test_auth_failure_exits_1_without_printing_the_token(creds, capsys):
    session = FakeSession([FakeResponse(401, {"err": "Token invalid", "ECODE": "OAUTH_025"})])

    assert main(["whoami"], session=session) == EXIT_ERROR

    captured = capsys.readouterr()
    assert "rejected the token" in captured.err
    assert "pk_test_token" not in captured.err + captured.out
    assert len(session.calls) == 1


def test_mine_filters_by_my_id_and_status(creds, capsys):
    routes = {
        ("GET", f"{V2}/user"): ME,
        ("GET", f"{V2}/team/9000001/task"): {
            "tasks": [{"id": "abc123", "name": "Fix login redirect", "status": {"status": "done"}}]
        },
    }
    session = FakeSession(router=_router(routes))

    assert main(["mine", "--status", "done", "--json"], session=session) == EXIT_OK

    params = session.calls[1]["params"]
    assert params["assignees[]"] == ["1001"]
    assert params["statuses[]"] == ["done"]
    assert params["include_closed"] == "true"
    assert json.loads(capsys.readouterr().out)["status"] == "done"


def test_search_matches_client_side_and_says_so(creds, capsys):
    routes = {
        ("GET", f"{V2}/team/9000001/task"): {
            "tasks": [
                {"id": "abc123", "name": "Fix login redirect"},
                {"id": "def456", "name": "Quarterly report draft"},
            ]
        }
    }
    session = FakeSession(router=_router(routes))

    assert main(["search", "LOGIN", "--json"], session=session) == EXIT_OK

    captured = capsys.readouterr()
    assert [json.loads(line)["id"] for line in captured.out.splitlines()] == ["abc123"]
    assert "no task search" in captured.err


def test_task_with_comments_json(creds, tmp_path, capsys):
    routes = {
        ("GET", f"{V2}/task/abc123"): {"id": "abc123", "name": "Fix login redirect"},
        ("GET", f"{V2}/user"): ME,
        ("GET", f"{V2}/team"): TEAMS,
        ("GET", f"{V2}/task/abc123/comment"): {
            "comments": [
                {
                    "id": "7",
                    "comment_text": "Old note",
                    "user": {"id": 1003, "username": "Bob Example"},
                    "date": "1700000000000",
                }
            ]
        },
    }
    session = FakeSession(router=_router(routes))

    code = main(["task", "abc123", "--comments", "--json"], session=session, raw_root=tmp_path)

    assert code == EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["task"]["id"] == "abc123"
    assert out["task"]["url"] == "https://app.clickup.com/t/abc123"
    # A task snapshot is not window-filtered: a 2023 comment still shows.
    assert [c["id"] for c in out["comments"]] == ["7"]


def test_chat_channels_and_messages(creds, tmp_path, capsys):
    channel = {"id": "ch-general", "name": "general", "type": "CHANNEL", "workspace_id": "9000001"}
    routes = {
        ("GET", f"{V3}/workspaces/9000001/chat/channels"): {"next_cursor": "", "data": [channel]},
        ("GET", f"{V2}/user"): ME,
        ("GET", f"{V2}/team"): TEAMS,
        ("GET", f"{V3}/workspaces/9000001/chat/channels/ch-general"): {"data": channel},
        ("GET", f"{V3}/workspaces/9000001/chat/channels/ch-general/messages"): {
            "next_cursor": "",
            "data": [
                {
                    "id": "m1",
                    "user_id": "1002",
                    "content": "hello",
                    "date": 1793800000000,
                    "parent_channel": "ch-general",
                    "replies_count": 0,
                }
            ],
        },
    }
    session = FakeSession(router=_router(routes))

    assert main(["chat", "channels", "--json"], session=session) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {
        "id": "ch-general",
        "name": "general",
        "type": "CHANNEL",
    }

    code = main(["chat", "messages", "ch-general", "--json"], session=session, raw_root=tmp_path)

    assert code == EXIT_OK
    message = json.loads(capsys.readouterr().out)
    assert (message["id"], message["container"]["name"]) == ("m1", "general")
    assert not WRITE_METHODS & set(session.methods)
