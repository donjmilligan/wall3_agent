"""notify.py: calls the platform notifier with a useful message; never fails."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOK = "notify.py"
EVENT = {"session_id": "s", "hook_event_name": "Stop", "cwd": "/work/wall3_agent"}


def fake_tools(tmp_path: Path, *names: str) -> tuple[Path, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    for name in names:
        tool = bindir / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        tool.chmod(0o755)
    return bindir, log


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS notifier selection")
def test_macos_prefers_terminal_notifier(run_hook, tmp_path):
    bindir, log = fake_tools(tmp_path, "terminal-notifier", "osascript")
    result = run_hook(HOOK, EVENT, env={"PATH": str(bindir)})
    assert result.returncode == 0
    assert calls(log) == ["terminal-notifier -title Claude Code -message Finished in wall3_agent"]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS notifier selection")
def test_macos_falls_back_to_osascript(run_hook, tmp_path):
    bindir, log = fake_tools(tmp_path, "osascript")
    result = run_hook(HOOK, EVENT, env={"PATH": str(bindir)})
    assert result.returncode == 0
    (line,) = calls(log)
    assert line.startswith('osascript -e display notification "Finished in wall3_agent"')


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux notifier")
def test_linux_uses_notify_send(run_hook, tmp_path):
    bindir, log = fake_tools(tmp_path, "notify-send")
    result = run_hook(HOOK, EVENT, env={"PATH": str(bindir)})
    assert result.returncode == 0
    assert calls(log) == ["notify-send Claude Code Finished in wall3_agent"]


def test_opt_out_makes_no_call(run_hook, tmp_path):
    bindir, log = fake_tools(tmp_path, "terminal-notifier", "osascript", "notify-send")
    result = run_hook(HOOK, EVENT, env={"PATH": str(bindir), "WALL3_NOTIFY": "0"})
    assert result.returncode == 0
    assert calls(log) == []
    # control: same tools, no opt-out, a call happens on macOS and Linux
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        run_hook(HOOK, EVENT, env={"PATH": str(bindir)})
        assert len(calls(log)) == 1


@pytest.mark.parametrize("stdin", ["", "not json", "[]"])
def test_no_notifier_and_bad_input_still_exit_zero(run_hook, tmp_path, stdin):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run_hook(HOOK, stdin, env={"PATH": str(empty)})
    assert result.returncode == 0
    assert "Traceback" not in result.stderr
