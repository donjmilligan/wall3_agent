"""Shared fixtures: run a hook the way Claude Code does — JSON on stdin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / ".claude" / "hooks"


@dataclass
class HookResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def json(self) -> dict[str, Any]:
        return json.loads(self.stdout)


RunHook = Callable[..., HookResult]


def _run(
    name: str,
    payload: dict[str, Any] | str,
    *,
    env: dict[str, str] | None = None,
    cwd: Path | str | None = None,
) -> HookResult:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    full_env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO), **(env or {})}
    proc = subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=stdin,
        capture_output=True,
        text=True,
        env=full_env,
        cwd=str(cwd) if cwd else None,
        timeout=60,
        check=False,
    )
    return HookResult(proc.returncode, proc.stdout, proc.stderr)


@pytest.fixture
def run_hook() -> RunHook:
    return _run


def bash_event(command: str, cwd: str | Path = REPO, event: str = "PreToolUse") -> dict[str, Any]:
    return {
        "session_id": "test-session",
        "transcript_path": "/dev/null",
        "cwd": str(cwd),
        "permission_mode": "default",
        "hook_event_name": event,
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "test"},
        "tool_use_id": "toolu_test",
    }


def mcp_event(
    tool_name: str, tool_input: dict[str, Any], event: str = "PreToolUse"
) -> dict[str, Any]:
    return {
        "session_id": "test-session",
        "cwd": str(REPO),
        "hook_event_name": event,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_test",
    }


@pytest.fixture
def make_bash_event() -> Callable[..., dict[str, Any]]:
    return bash_event


@pytest.fixture
def make_mcp_event() -> Callable[..., dict[str, Any]]:
    return mcp_event
