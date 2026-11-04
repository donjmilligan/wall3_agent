from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from wall3_slack.client import SlackClient

FIXTURES = Path(__file__).parent / "fixtures"
INBOX_FIXTURE = FIXTURES / "raw" / "inbox"
TOKEN = "xoxp-test-not-a-real-token"


class FakeResponse:
    def __init__(
        self,
        body: Any = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = {"ok": True} if body is None else body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


Responder = FakeResponse | Exception | Callable[[dict[str, Any]], FakeResponse]


class FakeSession:
    """Routes by Slack method name (the last URL segment) to queued responses.

    Records every request so tests can assert on exactly what was sent, and
    how many times.
    """

    def __init__(self, routes: dict[str, list[Responder]] | None = None) -> None:
        self.routes: dict[str, list[Responder]] = {k: list(v) for k, v in (routes or {}).items()}
        self.calls: list[dict[str, Any]] = []

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
    ) -> FakeResponse:
        api_method = url.rsplit("/", 1)[-1]
        call = {
            "http": method,
            "method": api_method,
            "url": url,
            "headers": dict(headers or {}),
            "params": params,
            "data": data,
            "json": json,
        }
        self.calls.append(call)
        queue = self.routes.get(api_method)
        if not queue:
            raise AssertionError(f"unexpected call to {api_method} with {params or data or json}")
        responder = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(responder, Exception):
            raise responder
        if callable(responder) and not isinstance(responder, FakeResponse):
            return responder(call)
        return responder

    def calls_to(self, api_method: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == api_method]


def make_client(
    routes: dict[str, list[Responder]] | None = None, *, max_retries: int = 3
) -> tuple[SlackClient, FakeSession, list[float]]:
    session = FakeSession(routes)
    sleeps: list[float] = []
    client = SlackClient(TOKEN, session=session, sleep=sleeps.append, max_retries=max_retries)
    return client, session, sleeps


@pytest.fixture
def inbox_fixture() -> Path:
    return INBOX_FIXTURE


@pytest.fixture(autouse=True)
def _isolate_token_env() -> Iterator[None]:
    """``load_dotenv`` writes os.environ directly; never let a token leak between tests."""
    saved = os.environ.pop("SLACK_USER_TOKEN", None)
    yield
    os.environ.pop("SLACK_USER_TOKEN", None)
    if saved is not None:
        os.environ["SLACK_USER_TOKEN"] = saved
