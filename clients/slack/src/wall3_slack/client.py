"""A thin, retrying Slack Web API client built on ``requests`` (no slack_sdk).

Facts this module depends on, verified against docs.slack.dev (see
ARCHITECTURE.md for URLs and dates):

* The token goes in ``Authorization: Bearer <token>``; never in the query
  string or a JSON body.
* Read methods accept GET with query parameters. Write methods accept a JSON
  body with an explicit ``Content-Type: application/json``.
* Every response is a JSON object with a boolean ``ok``; failures carry a
  machine-readable ``error`` code.
* Exceeding a rate limit returns HTTP 429 with ``Retry-After`` in seconds.
  ``ok: false, error: ratelimited`` is treated identically.
* Collections paginate with ``cursor`` / ``response_metadata.next_cursor``.
  ``search.messages`` paginates by ``page`` / ``messages.paging.pages``.

Retry policy:

* 429 / ``ratelimited``: retried for every method (the request was rejected,
  not processed), sleeping ``Retry-After``.
* HTTP 5xx and transport errors: retried with backoff **for reads only**. A
  write whose outcome is unknown is never re-sent -- re-sending a
  chat.postMessage that may have landed would post the message twice.
* Auth/scope errors: never retried; raised as ``SlackAuthError``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import requests

API_BASE = "https://slack.com/api"

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0
#: The longest single Retry-After we will sleep through. Anything longer is a
#: limit we should report, not silently wait out.
MAX_RETRY_AFTER_SECONDS = 120.0

#: Error codes that a retry cannot fix. Retrying them wastes rate budget and
#: hides the real problem.
AUTH_ERRORS = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "token_expired",
        "account_inactive",
        "missing_scope",
        "not_allowed_token_type",
        "no_permission",
    }
)

#: The user-token scope each method needs, used to name the missing scope when
#: Slack's ``needed`` field is absent. ``conversations.*`` reads need the
#: per-conversation-type scope; all four are listed.
METHOD_SCOPES: dict[str, str] = {
    "auth.test": "(none)",
    "conversations.list": "channels:read, groups:read, im:read, mpim:read",
    "conversations.info": "channels:read, groups:read, im:read, mpim:read",
    "users.conversations": "channels:read, groups:read, im:read, mpim:read",
    "conversations.history": "channels:history, groups:history, im:history, mpim:history",
    "conversations.replies": "channels:history, groups:history, im:history, mpim:history",
    "users.info": "users:read",
    "users.list": "users:read",
    "search.messages": "search:read",
    "chat.postMessage": "chat:write",
    "chat.getPermalink": "(none)",
}

WRITE_METHODS = frozenset({"chat.postMessage"})


class SlackAPIError(RuntimeError):
    """A Slack call failed (``ok: false``, non-2xx, or retries exhausted)."""

    def __init__(self, method: str, error: str, *, status: int | None = None) -> None:
        self.method = method
        self.error = error
        self.status = status
        where = f" (HTTP {status})" if status is not None else ""
        super().__init__(f"Slack {method} failed: {error}{where}")


class SlackAuthError(SlackAPIError):
    """The token is invalid/revoked or lacks a scope. Never retried."""

    def __init__(self, method: str, error: str, *, needed: str | None = None) -> None:
        self.needed = needed
        super().__init__(method, error)
        if error == "missing_scope":
            scope = needed or METHOD_SCOPES.get(method, "unknown")
            self.args = (
                f"Slack {method} failed: missing_scope -- the token needs user scope "
                f"'{scope}'. Add it under 'User Token Scopes' in the app's OAuth & "
                "Permissions page (or slack-app-manifest.yaml) and reinstall the app.",
            )
        else:
            self.args = (
                f"Slack {method} failed: {error} -- the user token is missing, "
                "invalid, or revoked. Check SLACK_USER_TOKEN (see clients/slack/README.md).",
            )


class SlackRateLimitError(SlackAPIError):
    """Rate limited past the retry budget, or asked to wait unreasonably long."""


class HttpResponse(Protocol):
    status_code: int
    headers: Any

    def json(self) -> Any: ...


class HttpSession(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> HttpResponse: ...


def _retry_after(headers: Any, attempt: int) -> float:
    value = headers.get("Retry-After") if headers is not None else None
    if value is not None:
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            pass
    return BACKOFF_BASE_SECONDS * (2**attempt)


class SlackClient:
    """Bearer-auth Slack Web API client with bounded retries."""

    def __init__(
        self,
        token: str,
        *,
        session: HttpSession | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self._session: HttpSession = session if session is not None else requests.Session()
        self._sleep = sleep
        self._max_retries = max_retries
        self._timeout = timeout
        self._user_cache: dict[str, dict[str, Any]] = {}

    def __repr__(self) -> str:  # never expose the token
        return "SlackClient(token=<redacted>)"

    # ------------------------------------------------------------------ core

    def call(
        self, method: str, params: dict[str, Any] | None = None, *, http: str = "GET"
    ) -> dict[str, Any]:
        """Call one Web API method and return its parsed JSON body.

        ``http`` is ``GET`` (query parameters), ``POST`` (form-encoded body) or
        ``POST_JSON`` (JSON body). Whether a failure may be retried depends on
        whether the method writes (``WRITE_METHODS``), not on the HTTP verb.
        """
        url = f"{API_BASE}/{method}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Authorization": f"Bearer {self._token}"}
        write = method in WRITE_METHODS
        if http == "POST_JSON":
            headers["Content-Type"] = "application/json; charset=utf-8"

        attempt = 0
        while True:
            try:
                if http == "POST_JSON":
                    response = self._session.request(
                        "POST", url, headers=headers, json=clean, timeout=self._timeout
                    )
                elif http == "POST":
                    response = self._session.request(
                        "POST", url, headers=headers, data=clean, timeout=self._timeout
                    )
                else:
                    response = self._session.request(
                        "GET", url, headers=headers, params=clean, timeout=self._timeout
                    )
            except requests.RequestException as exc:
                if write or attempt >= self._max_retries:
                    # A write may or may not have landed: surface, never resend.
                    raise SlackAPIError(method, f"transport error: {type(exc).__name__}") from exc
                self._sleep(BACKOFF_BASE_SECONDS * (2**attempt))
                attempt += 1
                continue

            status = response.status_code
            if status == 429:
                self._wait_for_rate_limit(method, response.headers, attempt)
                attempt += 1
                continue
            if status >= 500:
                if write or attempt >= self._max_retries:
                    raise SlackAPIError(method, "server_error", status=status)
                self._sleep(BACKOFF_BASE_SECONDS * (2**attempt))
                attempt += 1
                continue
            if status in (401, 403):
                raise SlackAuthError(method, "invalid_auth")

            try:
                body = response.json()
            except ValueError as exc:
                raise SlackAPIError(method, "invalid_json", status=status) from exc
            if not isinstance(body, dict):
                raise SlackAPIError(method, "invalid_json", status=status)

            if body.get("ok") is True:
                return body

            error = str(body.get("error") or "unknown_error")
            if error == "ratelimited":
                self._wait_for_rate_limit(method, response.headers, attempt)
                attempt += 1
                continue
            if error in AUTH_ERRORS:
                needed = body.get("needed")
                raise SlackAuthError(method, error, needed=needed if needed else None)
            raise SlackAPIError(method, error, status=status)

    def _wait_for_rate_limit(self, method: str, headers: Any, attempt: int) -> None:
        if attempt >= self._max_retries:
            raise SlackRateLimitError(method, "ratelimited (retries exhausted)", status=429)
        delay = _retry_after(headers, attempt)
        if delay > MAX_RETRY_AFTER_SECONDS:
            raise SlackRateLimitError(
                method, f"ratelimited (Retry-After {delay:.0f}s exceeds cap)", status=429
            )
        self._sleep(delay)

    def paginate(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int,
    ) -> Iterator[dict[str, Any]]:
        """Yield raw response pages following ``response_metadata.next_cursor``.

        Stops after ``max_pages``; callers detect truncation by checking the
        last page's ``next_cursor`` (see ``has_next_cursor``).
        """
        cursor: str | None = None
        for _ in range(max_pages):
            page = self.call(method, {**(params or {}), "cursor": cursor})
            yield page
            cursor = next_cursor(page)
            if not cursor:
                return

    # --------------------------------------------------------------- methods

    def auth_test(self) -> dict[str, Any]:
        # docs.slack.dev lists auth.test as POST; it takes no arguments.
        return self.call("auth.test", {}, http="POST")

    def conversations_list(
        self, types: str, *, cursor: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        return self.call(
            "conversations.list",
            {"types": types, "exclude_archived": "true", "limit": limit, "cursor": cursor},
        )

    def conversations_info(self, channel: str) -> dict[str, Any]:
        return self.call("conversations.info", {"channel": channel})

    def conversations_history(
        self,
        channel: str,
        *,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return self.call(
            "conversations.history",
            {
                "channel": channel,
                "oldest": oldest,
                "latest": latest,
                "limit": limit,
                "cursor": cursor,
            },
        )

    def conversations_replies(
        self,
        channel: str,
        ts: str,
        *,
        oldest: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return self.call(
            "conversations.replies",
            {"channel": channel, "ts": ts, "oldest": oldest, "limit": limit, "cursor": cursor},
        )

    def users_info(self, user: str) -> dict[str, Any]:
        """``users.info``, cached in-process for the life of this client."""
        cached = self._user_cache.get(user)
        if cached is not None:
            return cached
        body = self.call("users.info", {"user": user})
        self._user_cache[user] = body
        return body

    def users_list(self, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        return self.call("users.list", {"limit": limit, "cursor": cursor})

    def users_conversations(
        self, types: str, *, cursor: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        return self.call(
            "users.conversations",
            {"types": types, "exclude_archived": "true", "limit": limit, "cursor": cursor},
        )

    def search_messages(
        self,
        query: str,
        *,
        sort: str = "timestamp",
        sort_dir: str = "desc",
        count: int = 100,
        page: int | None = 1,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """``page`` paging by default; pass ``cursor="*"`` (and ``page=None``) for cursormark."""
        return self.call(
            "search.messages",
            {
                "query": query,
                "sort": sort,
                "sort_dir": sort_dir,
                "count": count,
                "page": page,
                "cursor": cursor,
            },
        )

    def chat_post_message(
        self, channel: str, text: str, *, thread_ts: str | None = None
    ) -> dict[str, Any]:
        """Post as the token's user. The only write this package performs."""
        return self.call(
            "chat.postMessage",
            {"channel": channel, "text": text, "thread_ts": thread_ts},
            http="POST_JSON",
        )

    def chat_get_permalink(self, channel: str, message_ts: str) -> dict[str, Any]:
        return self.call("chat.getPermalink", {"channel": channel, "message_ts": message_ts})


def next_cursor(page: dict[str, Any]) -> str | None:
    meta = page.get("response_metadata") or {}
    cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
    return cursor or None
