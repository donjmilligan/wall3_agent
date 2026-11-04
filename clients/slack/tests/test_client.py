from __future__ import annotations

import pytest
import requests
from conftest import TOKEN, FakeResponse, make_client

from wall3_slack.client import (
    MAX_RETRY_AFTER_SECONDS,
    SlackAPIError,
    SlackAuthError,
    SlackRateLimitError,
)


def test_bearer_auth_header_and_get_params():
    client, session, _ = make_client(
        {"conversations.history": [FakeResponse({"ok": True, "messages": []})]}
    )

    client.conversations_history("C000GENERAL", oldest="1793728800.000000", limit=50)

    (call,) = session.calls
    assert call["http"] == "GET"
    assert call["url"] == "https://slack.com/api/conversations.history"
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert call["params"] == {"channel": "C000GENERAL", "oldest": "1793728800.000000", "limit": 50}


def test_none_params_are_dropped_not_sent_as_the_string_none():
    client, session, _ = make_client({"conversations.history": [FakeResponse()]})

    client.conversations_history("C1")

    assert "oldest" not in session.calls[0]["params"]
    assert "cursor" not in session.calls[0]["params"]
    assert session.calls[0]["params"]["channel"] == "C1"


def test_auth_test_is_a_form_post():
    client, session, _ = make_client({"auth.test": [FakeResponse({"ok": True, "user_id": "U1"})]})

    assert client.auth_test()["user_id"] == "U1"
    assert session.calls[0]["http"] == "POST"
    assert session.calls[0]["json"] is None


def test_post_message_sends_json_body_with_content_type():
    client, session, _ = make_client(
        {"chat.postMessage": [FakeResponse({"ok": True, "channel": "D1", "ts": "1.000001"})]}
    )

    client.chat_post_message("D1", "hello", thread_ts="1.000000")

    (call,) = session.calls
    assert call["http"] == "POST"
    assert call["json"] == {"channel": "D1", "text": "hello", "thread_ts": "1.000000"}
    assert call["headers"]["Content-Type"].startswith("application/json")
    assert call["params"] is None  # token and text never travel in the query string


# --------------------------------------------------------------- rate limits


def test_http_429_sleeps_retry_after_then_succeeds():
    client, session, sleeps = make_client(
        {
            "search.messages": [
                FakeResponse(status_code=429, headers={"Retry-After": "7"}),
                FakeResponse({"ok": True, "messages": {"matches": []}}),
            ]
        }
    )

    client.search_messages("hello")

    assert sleeps == [7.0]
    assert len(session.calls) == 2


def test_ok_false_ratelimited_is_treated_like_429():
    client, session, sleeps = make_client(
        {
            "conversations.list": [
                FakeResponse({"ok": False, "error": "ratelimited"}, headers={"Retry-After": "3"}),
                FakeResponse({"ok": True, "channels": []}),
            ]
        }
    )

    assert client.conversations_list("im")["ok"] is True
    assert sleeps == [3.0]
    assert len(session.calls) == 2


def test_persistent_429_is_bounded_and_raises():
    client, session, sleeps = make_client(
        {"users.info": [FakeResponse(status_code=429, headers={"Retry-After": "1"})]},
        max_retries=3,
    )

    with pytest.raises(SlackRateLimitError):
        client.users_info("U1")

    assert len(session.calls) == 4  # first try + 3 retries
    assert sleeps == [1.0, 1.0, 1.0]


def test_unreasonable_retry_after_raises_instead_of_hanging():
    client, session, sleeps = make_client(
        {
            "users.info": [
                FakeResponse(
                    status_code=429, headers={"Retry-After": str(int(MAX_RETRY_AFTER_SECONDS) + 1)}
                )
            ]
        }
    )

    with pytest.raises(SlackRateLimitError, match="exceeds cap"):
        client.users_info("U1")

    assert sleeps == []
    assert len(session.calls) == 1


def test_429_without_retry_after_backs_off_exponentially():
    client, _, sleeps = make_client(
        {
            "users.info": [
                FakeResponse(status_code=429),
                FakeResponse(status_code=429),
                FakeResponse({"ok": True, "user": {"id": "U1"}}),
            ]
        }
    )

    client.users_info("U1")

    assert sleeps == [1.0, 2.0]


# -------------------------------------------------------- auth/scope errors


@pytest.mark.parametrize("code", ["invalid_auth", "not_authed", "token_revoked"])
def test_auth_errors_are_not_retried(code):
    client, session, sleeps = make_client(
        {"auth.test": [FakeResponse({"ok": False, "error": code})]}
    )

    with pytest.raises(SlackAuthError) as excinfo:
        client.auth_test()

    assert excinfo.value.error == code
    assert len(session.calls) == 1
    assert sleeps == []
    assert "SLACK_USER_TOKEN" in str(excinfo.value)


def test_missing_scope_names_the_needed_scope_from_the_response():
    """Slack's `needed` is more specific than our table (one scope, not four): it must win."""
    client, session, _ = make_client(
        {
            "conversations.history": [
                FakeResponse(
                    {"ok": False, "error": "missing_scope", "needed": "im:history", "provided": ""}
                )
            ]
        }
    )

    with pytest.raises(SlackAuthError) as excinfo:
        client.conversations_history("D000ALICE")

    assert len(session.calls) == 1
    assert "user scope 'im:history'" in str(excinfo.value)
    assert excinfo.value.needed == "im:history"


def test_missing_scope_falls_back_to_the_method_scope_table():
    client, _, _ = make_client(
        {"users.info": [FakeResponse({"ok": False, "error": "missing_scope"})]}
    )

    with pytest.raises(SlackAuthError, match="users:read"):
        client.users_info("U1")


def test_errors_never_contain_the_token():
    client, _, _ = make_client(
        {"auth.test": [FakeResponse({"ok": False, "error": "invalid_auth"})]}
    )

    with pytest.raises(SlackAuthError) as excinfo:
        client.auth_test()

    assert TOKEN not in str(excinfo.value)
    assert TOKEN not in repr(client)
    assert "redacted" in repr(client)


def test_other_ok_false_errors_raise_typed_error_with_code():
    client, session, _ = make_client(
        {"conversations.replies": [FakeResponse({"ok": False, "error": "thread_not_found"})]}
    )

    with pytest.raises(SlackAPIError) as excinfo:
        client.conversations_replies("C1", "1.0")

    assert excinfo.value.error == "thread_not_found"
    assert not isinstance(excinfo.value, SlackAuthError)
    assert len(session.calls) == 1


# ---------------------------------------------- reads retry, writes never do


def test_read_retries_5xx():
    client, session, sleeps = make_client(
        {"users.info": [FakeResponse(status_code=503), FakeResponse({"ok": True, "user": {}})]}
    )

    client.users_info("U1")

    assert len(session.calls) == 2
    assert sleeps == [1.0]


def test_write_is_not_resent_after_5xx():
    """A 5xx on chat.postMessage may have posted. Re-sending would duplicate it."""
    client, session, sleeps = make_client(
        {"chat.postMessage": [FakeResponse(status_code=503), FakeResponse({"ok": True})]}
    )

    with pytest.raises(SlackAPIError):
        client.chat_post_message("D1", "hi")

    assert len(session.calls_to("chat.postMessage")) == 1
    assert sleeps == []


def test_write_is_not_resent_after_timeout():
    client, session, _ = make_client(
        {"chat.postMessage": [requests.Timeout("slow"), FakeResponse({"ok": True})]}
    )

    with pytest.raises(SlackAPIError, match="transport error"):
        client.chat_post_message("D1", "hi")

    assert len(session.calls_to("chat.postMessage")) == 1


def test_read_retries_after_timeout():
    client, session, _ = make_client(
        {"users.info": [requests.Timeout("slow"), FakeResponse({"ok": True, "user": {}})]}
    )

    client.users_info("U1")

    assert len(session.calls) == 2


def test_write_is_retried_on_429_because_it_was_not_processed():
    client, session, _ = make_client(
        {
            "chat.postMessage": [
                FakeResponse(status_code=429, headers={"Retry-After": "1"}),
                FakeResponse({"ok": True, "channel": "D1", "ts": "1.0"}),
            ]
        }
    )

    assert client.chat_post_message("D1", "hi")["ts"] == "1.0"
    assert len(session.calls_to("chat.postMessage")) == 2


# ------------------------------------------------------------ pagination


def test_paginate_follows_next_cursor_until_empty():
    client, session, _ = make_client(
        {
            "conversations.list": [
                FakeResponse(
                    {
                        "ok": True,
                        "channels": [{"id": "D1"}],
                        "response_metadata": {"next_cursor": "abc"},
                    }
                ),
                FakeResponse(
                    {
                        "ok": True,
                        "channels": [{"id": "D2"}],
                        "response_metadata": {"next_cursor": ""},
                    }
                ),
            ]
        }
    )

    pages = list(client.paginate("conversations.list", {"types": "im"}, max_pages=10))

    assert [p["channels"][0]["id"] for p in pages] == ["D1", "D2"]
    assert "cursor" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["cursor"] == "abc"


def test_paginate_stops_at_max_pages():
    page = FakeResponse({"ok": True, "channels": [], "response_metadata": {"next_cursor": "more"}})
    client, session, _ = make_client({"conversations.list": [page]})

    pages = list(client.paginate("conversations.list", {}, max_pages=3))

    assert len(pages) == 3
    assert len(session.calls) == 3


def test_users_info_is_cached_in_process():
    client, session, _ = make_client(
        {"users.info": [FakeResponse({"ok": True, "user": {"id": "U1", "name": "a"}})]}
    )

    first = client.users_info("U1")
    second = client.users_info("U1")

    assert first == second == {"ok": True, "user": {"id": "U1", "name": "a"}}
    assert len(session.calls) == 1


def test_search_supports_page_and_cursor_modes():
    client, session, _ = make_client({"search.messages": [FakeResponse()]})

    client.search_messages("q", page=2)
    client.search_messages("q", page=None, cursor="*")

    assert session.calls[0]["params"]["page"] == 2
    assert "cursor" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["cursor"] == "*"
    assert "page" not in session.calls[1]["params"]
