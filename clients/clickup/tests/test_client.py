from __future__ import annotations

import pytest
import requests

from tests.conftest import FakeResponse, FakeSession, make_client
from wall3_clickup.client import (
    API_V2,
    API_V3,
    ClickUpAPIError,
    ClickUpAuthError,
    ClickUpClient,
    SilentStatusChange,
)
from wall3_clickup.config import ClickUpConfig

# ---------------------------------------------------------------- transport


def test_sends_token_verbatim_in_authorization_header():
    client, session, _ = make_client([FakeResponse(200, {"user": {"id": 1}})])

    assert client.get_user() == {"user": {"id": 1}}

    assert session.calls[0]["url"] == "https://api.clickup.com/api/v2/user"
    assert session.calls[0]["headers"]["Authorization"] == "pk_test_token"


def test_429_sleeps_until_rate_limit_reset_then_retries():
    # clock = 1_000_000; reset 12s later -> sleep 12.
    client, session, sleeps = make_client(
        [
            FakeResponse(429, {"err": "Rate limit"}, headers={"X-RateLimit-Reset": "1000012"}),
            FakeResponse(200, {"teams": []}),
        ]
    )

    assert client.get_teams() == {"teams": []}
    assert sleeps == [12.0]
    assert len(session.calls) == 2


def test_429_falls_back_to_retry_after():
    client, _, sleeps = make_client(
        [FakeResponse(429, {}, headers={"Retry-After": "3"}), FakeResponse(200, {"ok": 1})]
    )

    client.get_teams()

    assert sleeps == [3.0]


def test_429_sleep_is_capped():
    client, _, sleeps = make_client(
        [
            FakeResponse(429, {}, headers={"X-RateLimit-Reset": "1009999"}),
            FakeResponse(200, {"ok": 1}),
        ]
    )

    client.get_teams()

    assert sleeps == [65.0]


def test_persistent_429_exhausts_retries_and_raises():
    client, session, sleeps = make_client(
        [FakeResponse(429, {}, headers={"Retry-After": "1"}) for _ in range(3)], max_retries=3
    )

    with pytest.raises(ClickUpAPIError, match="exhausted 3 attempts"):
        client.get_teams()

    assert len(session.calls) == 3
    assert sleeps == [1.0, 1.0]  # no pointless sleep after the final attempt


def test_5xx_on_get_backs_off_exponentially_then_succeeds():
    client, session, sleeps = make_client(
        [
            FakeResponse(502, {}),
            FakeResponse(503, {}),
            FakeResponse(500, {}),
            FakeResponse(200, {}),
        ],
    )

    client.get_task("abc123")

    assert sleeps == [1.0, 2.0, 4.0]
    assert len(session.calls) == 4


def test_5xx_backoff_is_capped():
    client, _, sleeps = make_client([FakeResponse(500, {}) for _ in range(7)], max_retries=7)

    with pytest.raises(ClickUpAPIError):
        client.get_task("abc123")

    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


def test_5xx_on_post_is_not_retried_to_avoid_double_posting():
    client, session, sleeps = make_client([FakeResponse(500, {}), FakeResponse(200, {"id": "1"})])

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.add_comment("abc123", "hello")

    assert excinfo.value.status == 500
    assert session.methods == ["POST"]
    assert sleeps == []


def test_5xx_on_put_is_retried_because_put_is_idempotent():
    client, session, sleeps = make_client([FakeResponse(503, {}), FakeResponse(200, {})])

    client.update_task_status("abc123", "done", allow_silent_status=True)

    assert session.methods == ["PUT", "PUT"]
    assert sleeps == [1.0]


def test_429_on_post_is_retried_because_the_request_was_rejected():
    client, session, _ = make_client(
        [FakeResponse(429, {}, headers={"Retry-After": "1"}), FakeResponse(200, {"id": "9"})]
    )

    assert client.add_comment("abc123", "hello")["id"] == "9"
    assert session.methods == ["POST", "POST"]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_raise_distinct_error_and_are_never_retried(status):
    client, session, sleeps = make_client([FakeResponse(status, {"err": "Token invalid"})] * 3)

    with pytest.raises(ClickUpAuthError) as excinfo:
        client.get_user()

    assert excinfo.value.status == status
    assert len(session.calls) == 1
    assert sleeps == []


def test_other_4xx_raises_plain_api_error():
    client, session, _ = make_client([FakeResponse(404, {"err": "Task not found"})])

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.get_task("nope")

    assert not isinstance(excinfo.value, ClickUpAuthError)
    assert excinfo.value.status == 404
    assert len(session.calls) == 1


def test_connection_error_on_get_is_retried():
    class Flaky(FakeSession):
        def request(self, method, url, **kwargs):  # noqa: ANN001
            self.calls.append({"method": method, "url": url})
            if len(self.calls) == 1:
                raise requests.ConnectionError("reset")
            return FakeResponse(200, {"ok": 1})

    session = Flaky()
    sleeps: list[float] = []
    client = ClickUpClient(ClickUpConfig(token="t"), session=session, sleep=sleeps.append)

    assert client.get_teams() == {"ok": 1}
    assert len(session.calls) == 2
    assert sleeps == [1.0]


def test_error_message_never_contains_the_token():
    client, _, _ = make_client([FakeResponse(404, {"err": "nope"})])

    with pytest.raises(ClickUpAPIError) as excinfo:
        client.get_task("abc123")

    assert "pk_test_token" not in str(excinfo.value)
    assert "abc123" in str(excinfo.value)


def test_config_repr_hides_the_token():
    config = ClickUpConfig(token="pk_secret_value", team_id="9000001")

    assert "pk_secret_value" not in repr(config)
    assert "9000001" in repr(config)


# ---------------------------------------------------------------- pagination


def _tasks(n: int, start: int = 0) -> dict:
    return {"tasks": [{"id": f"t{i}"} for i in range(start, start + n)]}


def test_team_tasks_paginates_until_a_short_page_and_sends_filters():
    client, session, _ = make_client([FakeResponse(200, _tasks(100)), FakeResponse(200, _tasks(3))])

    pages = list(
        client.iter_team_task_pages(
            "9000001",
            max_pages=5,
            assignees=["1001"],
            statuses=["in progress"],
            date_updated_gt=1234,
            include_closed=True,
        )
    )

    assert [len(p["tasks"]) for p in pages] == [100, 3]
    assert [c["params"]["page"] for c in session.calls] == [0, 1]
    first = session.calls[0]
    assert first["url"] == f"{API_V2}/team/9000001/task"
    assert first["params"]["assignees[]"] == ["1001"]
    assert first["params"]["statuses[]"] == ["in progress"]
    assert first["params"]["date_updated_gt"] == 1234
    assert first["params"]["include_closed"] == "true"
    assert first["params"]["subtasks"] == "true"


def test_team_tasks_stops_at_max_pages():
    client, session, _ = make_client([FakeResponse(200, _tasks(100)) for _ in range(3)])

    pages = list(client.iter_team_task_pages("9000001", max_pages=2))

    assert len(pages) == 2
    assert len(session.calls) == 2


def test_team_tasks_omits_unset_filters():
    client, session, _ = make_client([FakeResponse(200, _tasks(0))])

    list(client.iter_team_task_pages("9000001", max_pages=1))

    params = session.calls[0]["params"]
    assert "assignees[]" not in params and "statuses[]" not in params
    assert "date_updated_gt" not in params
    assert params["page"] == 0  # the request did happen


def _comments(ids_dates: list[tuple[str, int]]) -> dict:
    return {"comments": [{"id": cid, "date": str(d)} for cid, d in ids_dates]}


def test_task_comments_paginate_with_start_and_start_id_of_last_comment():
    full = [(str(900 - i), 5000 - i) for i in range(25)]  # newest first
    client, session, _ = make_client(
        [FakeResponse(200, _comments(full)), FakeResponse(200, _comments([("1", 10)]))]
    )

    pages = list(client.iter_task_comment_pages("abc123", max_pages=5))

    assert len(pages) == 2
    assert session.calls[0]["url"] == f"{API_V2}/task/abc123/comment"
    assert session.calls[0]["params"] is None
    assert session.calls[1]["params"] == {"start": 4976, "start_id": "876"}


def test_task_comments_stop_once_older_than_window():
    full = [(str(900 - i), 5000 - i) for i in range(25)]
    client, session, _ = make_client([FakeResponse(200, _comments(full))])

    pages = list(client.iter_task_comment_pages("abc123", max_pages=5, stop_before_ms=4990))

    assert len(pages) == 1
    assert len(session.calls) == 1


def test_chat_channels_follow_next_cursor_until_empty():
    client, session, _ = make_client(
        [
            FakeResponse(200, {"next_cursor": "c2", "data": [{"id": "a"}]}),
            FakeResponse(200, {"next_cursor": "", "data": [{"id": "b"}]}),
        ]
    )

    pages = list(client.iter_chat_channel_pages("9000001", max_pages=5, with_message_since=77))

    assert [p["data"][0]["id"] for p in pages] == ["a", "b"]
    assert session.calls[0]["url"] == f"{API_V3}/workspaces/9000001/chat/channels"
    assert session.calls[0]["params"] == {"limit": 50, "with_message_since": 77}
    assert session.calls[1]["params"]["cursor"] == "c2"


def test_chat_messages_and_replies_endpoints_request_plain_text():
    client, session, _ = make_client(
        [
            FakeResponse(200, {"next_cursor": "", "data": [{"id": "m1"}]}),
            FakeResponse(200, {"next_cursor": "", "data": [{"id": "r1"}]}),
            FakeResponse(200, {"next_cursor": "", "data": []}),
        ]
    )

    list(client.iter_chat_message_pages("9000001", "ch1", max_pages=1))
    list(client.iter_chat_reply_pages("9000001", "m1", max_pages=1))
    client.get_chat_tagged_users("9000001", "m1")

    assert [c["url"] for c in session.calls] == [
        f"{API_V3}/workspaces/9000001/chat/channels/ch1/messages",
        f"{API_V3}/workspaces/9000001/chat/messages/m1/replies",
        f"{API_V3}/workspaces/9000001/chat/messages/m1/tagged_users",
    ]
    assert session.calls[0]["params"]["content_format"] == "text/plain"
    assert session.calls[1]["params"]["content_format"] == "text/plain"


# ---------------------------------------------------------------- writes + guard


def test_add_comment_posts_required_body():
    client, session, _ = make_client([FakeResponse(200, {"id": "458", "hist_id": "1", "date": 1})])

    assert client.add_comment("abc123", "Looks good.")["id"] == "458"

    call = session.calls[0]
    assert (call["method"], call["url"]) == ("POST", f"{API_V2}/task/abc123/comment")
    assert call["json"] == {"comment_text": "Looks good.", "notify_all": False}


def test_status_change_without_a_comment_is_refused_before_any_request():
    client, session, _ = make_client([FakeResponse(200, {})])

    with pytest.raises(SilentStatusChange, match="abc123"):
        client.update_task_status("abc123", "complete")

    assert session.calls == []


def test_status_change_after_commenting_on_that_task_is_sent():
    client, session, _ = make_client([FakeResponse(200, {"id": "1"}), FakeResponse(200, {})])

    client.add_comment("abc123", "Done: see PR.")
    client.update_task_status("abc123", "complete")

    assert session.methods == ["POST", "PUT"]
    assert session.calls[1]["url"] == f"{API_V2}/task/abc123"
    assert session.calls[1]["json"] == {"status": "complete"}


def test_a_comment_on_one_task_does_not_unlock_another():
    client, session, _ = make_client([FakeResponse(200, {"id": "1"})])
    client.add_comment("abc123", "unrelated")

    with pytest.raises(SilentStatusChange):
        client.update_task_status("def456", "complete")

    assert session.methods == ["POST"]


def test_allow_silent_status_is_the_explicit_escape():
    client, session, _ = make_client([FakeResponse(200, {})])

    client.update_task_status("abc123", "complete", allow_silent_status=True)

    assert session.methods == ["PUT"]
