#!/usr/bin/env python3
"""Stop hook: desktop notification that Claude has finished.

macOS: ``terminal-notifier`` if installed, else ``osascript``. Linux:
``notify-send``. Anything missing or failing is ignored. Set
``WALL3_NOTIFY=0`` to silence it. Always exits 0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

TITLE = "Claude Code"


def message_from_stdin() -> str:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        event = {}
    cwd = event.get("cwd") if isinstance(event, dict) else None
    if not isinstance(cwd, str) or not cwd:
        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return f"Finished in {os.path.basename(cwd.rstrip('/')) or cwd}"


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def commands(message: str) -> list[list[str]]:
    if sys.platform == "darwin":
        if shutil.which("terminal-notifier"):
            return [["terminal-notifier", "-title", TITLE, "-message", message]]
        if shutil.which("osascript"):
            script = (
                f"display notification {applescript_string(message)} "
                f"with title {applescript_string(TITLE)}"
            )
            return [["osascript", "-e", script]]
        return []
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        return [["notify-send", TITLE, message]]
    return []


def main() -> int:
    message = message_from_stdin()
    if os.environ.get("WALL3_NOTIFY") == "0":
        return 0
    for argv in commands(message):
        try:
            subprocess.run(argv, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
