"""Retrying ClickUp REST client (API v2 + the v3 Chat API).

Auth is a personal API token sent verbatim in the `Authorization` header
(https://developer.clickup.com/docs/authentication). Rate limits are per token
-- 100 req/min on Free/Unlimited/Business plans -- and a 429 carries
`X-RateLimit-Reset` as a Unix timestamp in seconds
(https://developer.clickup.com/docs/rate-limits).

Every method returns the API's JSON *verbatim* (a dict). Shaping happens in
`normalize/`, never here; `extract/` writes these dicts to disk unchanged.

Retry policy:

* 429 -> sleep until `X-RateLimit-Reset` (else `Retry-After`, else a default),
  capped, then retry. A 429 means the request was rejected, so retrying is
  safe for every method, writes included.
* 5xx / connection error -> capped exponential backoff, **only for idempotent
  methods** (GET, PUT, DELETE). A POST that hit a 5xx may still have created
  the comment; retrying it could post the same text twice to a real person.
* 401 / 403 -> `ClickUpAuthError` immediately, never retried: the same token
  will fail the same way.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import requests

from .config import ClickUpConfig

API_V2 = "https://api.clickup.com/api/v2"
API_V3 = "https://api.clickup.com/api/v3"

#: GET /v2/team/{team_id}/task returns at most 100 tasks per page.
TASK_PAGE_SIZE = 100
#: GET /v2/task/{task_id}/comment returns the 25 most recent comments by default.
COMMENT_PAGE_SIZE = 25
#: v3 chat list endpoints: limit 1..100, default 50.
CHAT_PAGE_LIMIT = 50

MAX_BACKOFF_SECONDS = 30.0
#: The rate-limit window is one minute; never sleep longer than that plus slack.
MAX_RATE_LIMIT_SLEEP_SECONDS = 65.0
DEFAULT_RATE_LIMIT_SLEEP_SECONDS = 10.0

_IDEMPOTENT = frozenset({"GET", "PUT", "DELETE", "HEAD", "OPTIONS"})


class ClickUpAPIError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        super().__init__(f"{method} {url} -> HTTP {status}: {body[:500]}")
        self.method = method
        self.url = url
        self.status = status
        self.body = body


class ClickUpAuthError(ClickUpAPIError):
    """401/403: bad, revoked or under-privileged token. Never retried."""


class SilentStatusChange(RuntimeError):
    """A status change was attempted on a task this client has not commented on.

    Every status change carries a comment -- intent, reason or result -- so a
    person opening the task can see why it moved. The CLI's `status` verb
    always posts its `--comment-file` first, which satisfies this guard; the
    guard exists so no other code path can flip a status silently.
    `allow_silent_status=True` is the deliberate, typed-on-purpose escape.
    """


class HttpSession(Protocol):
    """What the client needs from a session; `requests.Session` satisfies it."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> Any: ...


def _bool(value: bool) -> str:
    return "true" if value else "false"


class ClickUpClient:
    def __init__(
        self,
        config: ClickUpConfig,
        *,
        session: HttpSession | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._session: HttpSession = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        # Task ids this client instance has commented on; see SilentStatusChange.
        self._commented: set[str] = set()

    # ------------------------------------------------------------------ transport

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": self._config.token, "Content-Type": "application/json"}
        retry_on_server_error = method.upper() in _IDEMPOTENT
        last_error = "no attempt made"
        attempts = max(self._config.max_retries, 1)
        for attempt in range(attempts):
            final = attempt == attempts - 1
            try:
                response = self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self._config.timeout_seconds,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if not retry_on_server_error:
                    raise ClickUpAPIError(method, url, 0, f"network error: {exc}") from exc
                last_error = f"network error: {exc}"
                if not final:
                    self._sleep(self._backoff(attempt))
                continue

            status = response.status_code
            if status in (401, 403):
                raise ClickUpAuthError(method, url, status, response.text)
            if status == 429:
                last_error = "rate limited (429)"
                if not final:
                    self._sleep(self._rate_limit_delay(response))
                continue
            if status >= 500:
                if not retry_on_server_error:
                    raise ClickUpAPIError(method, url, status, response.text)
                last_error = f"server error ({status})"
                if not final:
                    self._sleep(self._backoff(attempt))
                continue
            if not response.ok:
                raise ClickUpAPIError(method, url, status, response.text)
            if not response.content:
                return {}
            return response.json()
        # No sleep after the final attempt: waiting only to raise wastes time.
        raise ClickUpAPIError(method, url, 0, f"exhausted {attempts} attempts; last: {last_error}")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0**attempt, MAX_BACKOFF_SECONDS)

    def _rate_limit_delay(self, response: Any) -> float:
        headers = response.headers or {}
        reset = headers.get("X-RateLimit-Reset")
        if reset:
            try:
                delay = float(reset) - self._clock()
                return min(max(delay, 1.0), MAX_RATE_LIMIT_SLEEP_SECONDS)
            except ValueError:
                pass
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), MAX_RATE_LIMIT_SLEEP_SECONDS)
            except ValueError:
                pass
        return DEFAULT_RATE_LIMIT_SLEEP_SECONDS

    # ------------------------------------------------------------------ v2 reads

    def get_user(self) -> dict[str, Any]:
        """https://developer.clickup.com/reference/getauthorizeduser -- GET /v2/user."""
        return self._request("GET", f"{API_V2}/user")

    def get_teams(self) -> dict[str, Any]:
        """https://developer.clickup.com/reference/getauthorizedteams -- GET /v2/team.

        "Teams" are Workspaces in API v2; `teams[].members[].user` carries
        `id`/`username`, which is how chat `user_id`s are resolved to names.
        """
        return self._request("GET", f"{API_V2}/team")

    def get_task(self, task_id: str) -> dict[str, Any]:
        """https://developer.clickup.com/reference/gettask -- GET /v2/task/{task_id}."""
        return self._request("GET", f"{API_V2}/task/{task_id}")

    def iter_task_comment_pages(
        self, task_id: str, *, max_pages: int, stop_before_ms: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """https://developer.clickup.com/reference/gettaskcomments -- GET /v2/task/{id}/comment.

        Newest first, 25 per page. The next (older) page is requested with
        `start` = the last comment's `date` and `start_id` = its `id`
        (https://developer.clickup.com/docs/task-comments-pagination).
        Stops on a short page, `max_pages`, or once the oldest comment on a
        page is older than `stop_before_ms`.
        """
        params: dict[str, Any] = {}
        for _ in range(max_pages):
            page = self._request("GET", f"{API_V2}/task/{task_id}/comment", params=params or None)
            yield page
            comments = page.get("comments") or []
            if len(comments) < COMMENT_PAGE_SIZE:
                return
            last = comments[-1]
            if stop_before_ms is not None and int(last.get("date") or 0) < stop_before_ms:
                return
            params = {"start": int(last["date"]), "start_id": str(last["id"])}

    def get_comment_replies(self, comment_id: str) -> dict[str, Any]:
        """https://developer.clickup.com/reference/getthreadedcomments.

        GET /v2/comment/{comment_id}/reply. The parent comment is not included.
        """
        return self._request("GET", f"{API_V2}/comment/{comment_id}/reply")

    def iter_team_task_pages(
        self,
        team_id: str,
        *,
        max_pages: int,
        assignees: list[str] | None = None,
        statuses: list[str] | None = None,
        date_updated_gt: int | None = None,
        include_closed: bool = False,
        subtasks: bool = True,
        order_by: str = "updated",
    ) -> Iterator[dict[str, Any]]:
        """https://developer.clickup.com/reference/getfilteredteamtasks.

        GET /v2/team/{team_id}/task. 100 tasks per page, `page` starts at 0;
        a page shorter than 100 is the last. Array filters are sent as
        repeated `assignees[]=` / `statuses[]=` keys.
        """
        for page_number in range(max_pages):
            params: dict[str, Any] = {
                "page": page_number,
                "order_by": order_by,
                "include_closed": _bool(include_closed),
                "subtasks": _bool(subtasks),
            }
            if assignees:
                params["assignees[]"] = list(assignees)
            if statuses:
                params["statuses[]"] = list(statuses)
            if date_updated_gt is not None:
                params["date_updated_gt"] = date_updated_gt
            page = self._request("GET", f"{API_V2}/team/{team_id}/task", params=params)
            yield page
            if len(page.get("tasks") or []) < TASK_PAGE_SIZE:
                return

    # ------------------------------------------------------------------ v3 chat reads
    # The Chat API is documented as experimental and subject to change
    # (https://developer.clickup.com/docs/chat).

    def _iter_cursor_pages(
        self, url: str, params: dict[str, Any], *, max_pages: int
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        for _ in range(max_pages):
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            page = self._request("GET", url, params=page_params)
            yield page
            next_cursor = page.get("next_cursor")
            if not next_cursor or next_cursor == cursor or not page.get("data"):
                return
            cursor = next_cursor

    def iter_chat_channel_pages(
        self, workspace_id: str, *, max_pages: int, with_message_since: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """https://developer.clickup.com/reference/getchatchannels.

        GET /api/v3/workspaces/{workspace_id}/chat/channels (cursor-paginated).
        """
        params: dict[str, Any] = {"limit": CHAT_PAGE_LIMIT}
        if with_message_since is not None:
            params["with_message_since"] = with_message_since
        yield from self._iter_cursor_pages(
            f"{API_V3}/workspaces/{workspace_id}/chat/channels", params, max_pages=max_pages
        )

    def get_chat_channel(self, workspace_id: str, channel_id: str) -> dict[str, Any]:
        """https://developer.clickup.com/reference/getchatchannel.

        GET /api/v3/workspaces/{workspace_id}/chat/channels/{channel_id} -> {"data": {...}}.
        """
        url = f"{API_V3}/workspaces/{workspace_id}/chat/channels/{channel_id}"
        return self._request("GET", url)

    def iter_chat_message_pages(
        self, workspace_id: str, channel_id: str, *, max_pages: int
    ) -> Iterator[dict[str, Any]]:
        """https://developer.clickup.com/reference/getchatmessages.

        GET /api/v3/workspaces/{workspace_id}/chat/channels/{channel_id}/messages.
        `content_format=text/plain` (the default is text/md).
        """
        yield from self._iter_cursor_pages(
            f"{API_V3}/workspaces/{workspace_id}/chat/channels/{channel_id}/messages",
            {"limit": CHAT_PAGE_LIMIT, "content_format": "text/plain"},
            max_pages=max_pages,
        )

    def iter_chat_reply_pages(
        self, workspace_id: str, message_id: str, *, max_pages: int
    ) -> Iterator[dict[str, Any]]:
        """https://developer.clickup.com/reference/getchatmessagereplies.

        GET /api/v3/workspaces/{workspace_id}/chat/messages/{message_id}/replies.
        """
        yield from self._iter_cursor_pages(
            f"{API_V3}/workspaces/{workspace_id}/chat/messages/{message_id}/replies",
            {"limit": CHAT_PAGE_LIMIT, "content_format": "text/plain"},
            max_pages=max_pages,
        )

    def get_chat_tagged_users(self, workspace_id: str, message_id: str) -> dict[str, Any]:
        """https://developer.clickup.com/reference/getchatmessagetaggedusers.

        GET /api/v3/workspaces/{workspace_id}/chat/messages/{message_id}/tagged_users.
        First page only (limit 100): a message tagging >100 people is not a
        case worth a second request.
        """
        return self._request(
            "GET",
            f"{API_V3}/workspaces/{workspace_id}/chat/messages/{message_id}/tagged_users",
            params={"limit": 100},
        )

    # ------------------------------------------------------------------ writes

    def add_comment(self, task_id: str, text: str, *, notify_all: bool = False) -> dict[str, Any]:
        """https://developer.clickup.com/reference/createtaskcomment.

        POST /v2/task/{task_id}/comment with {"comment_text", "notify_all"}
        (both required). Returns {"id", "hist_id", "date"}.
        """
        result = self._request(
            "POST",
            f"{API_V2}/task/{task_id}/comment",
            json_body={"comment_text": text, "notify_all": notify_all},
        )
        self._commented.add(task_id)
        return result

    def update_task_status(
        self, task_id: str, status: str, *, allow_silent_status: bool = False
    ) -> dict[str, Any]:
        """https://developer.clickup.com/reference/updatetask -- PUT /v2/task/{task_id}.

        Refused (before any request) unless this client already commented on
        `task_id`; see SilentStatusChange.
        """
        if not allow_silent_status and task_id not in self._commented:
            raise SilentStatusChange(
                f"Refusing to set {task_id} to {status!r} with no comment behind it. "
                f"Call add_comment({task_id!r}, ...) first, or pass "
                f"allow_silent_status=True for a deliberate exception."
            )
        return self._request("PUT", f"{API_V2}/task/{task_id}", json_body={"status": status})
