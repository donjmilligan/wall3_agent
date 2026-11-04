from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wall3_clickup import config as config_module
from wall3_clickup.client import ClickUpClient
from wall3_clickup.config import ClickUpConfig

FIXTURES = Path(__file__).parent / "fixtures"
INBOX_RAW = FIXTURES / "raw" / "inbox"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """No test may read a developer's real .env or exported token.

    `load_dotenv` writes into os.environ, so a real repo .env would leak a real
    token into the test process. Only tests marked `real_env_search` (which
    build their own throwaway git repos) use the real candidate search.
    """
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    monkeypatch.delenv("CLICKUP_TEAM_ID", raising=False)
    if request.node.get_closest_marker("real_env_search") is None:
        monkeypatch.setattr(config_module, "_candidate_env_files", lambda: [])


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(payload)
        self.content = b"" if payload is None else json.dumps(payload).encode()

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self) -> Any:
        return self._payload


Responder = Callable[[str, str, dict[str, Any] | None], FakeResponse]


class FakeSession:
    """Records every request. Serves a queue of responses, or routes via a function."""

    def __init__(
        self, responses: list[FakeResponse] | None = None, router: Responder | None = None
    ) -> None:
        self._responses = list(responses or [])
        self._router = router
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers=None, params=None, json=None, timeout=None):  # noqa: ANN001
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "json": json}
        )
        if self._router is not None:
            return self._router(method, url, params)
        if not self._responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self._responses.pop(0)

    @property
    def methods(self) -> list[str]:
        return [c["method"] for c in self.calls]


def make_client(
    responses: list[FakeResponse] | None = None,
    *,
    max_retries: int = 4,
    clock: float = 1_000_000.0,
) -> tuple[ClickUpClient, FakeSession, list[float]]:
    session = FakeSession(responses)
    sleeps: list[float] = []
    client = ClickUpClient(
        ClickUpConfig(token="pk_test_token", team_id="9000001", max_retries=max_retries),
        session=session,
        sleep=sleeps.append,
        clock=lambda: clock,
    )
    return client, session, sleeps
