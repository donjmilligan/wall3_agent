#!/usr/bin/env python3
"""PreToolUse hook: no Slack/ClickUp write runs without Don's approval.

* Bash calls that invoke a write verb of the ``slack`` / ``clickup`` CLIs
  (``bin/slack send``, ``uv run ... clickup comment``, ``python -m wall3_slack
  send`` ...) get ``permissionDecision: "ask"``. The reason names the target
  and shows the exact text, read from ``--text-file`` / ``--comment-file``.
* MCP tools of a Slack or ClickUp connector whose action is not a read get
  ``"ask"`` with the payload.
* Everything else passes silently (exit 0, no output), so normal permission
  rules apply.

Output schema (PreToolUse ``hookSpecificOutput``): see .claude/hooks/README.md.
If evaluating a command that mentions slack/clickup raises, the hook asks
rather than passing: a guard that crashes open is no guard.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hooklib  # noqa: E402

TEXT_LIMIT = 1200
FILE_READ_LIMIT = 64 * 1024


def ask(reason: str) -> None:
    hooklib.emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        }
    )


def read_text_file(path: str, cwd: str) -> str:
    if path == "-":
        return "(text comes from stdin; cannot preview it — inspect the command)"
    full = path if os.path.isabs(path) else os.path.join(cwd, path)
    try:
        with open(full, "rb") as fh:
            data = fh.read(FILE_READ_LIMIT)
    except OSError as exc:
        return f"(could not read {path}: {exc.strerror or exc})"
    return data.decode("utf-8", errors="replace")


def describe_cli(call: hooklib.CliCall, cwd: str) -> str:
    pos = call.positionals
    lines = [f"OUTBOUND {call.source.upper()} WRITE: `{call.program} {' '.join(pos[:1])}`"]
    if call.source == "slack":
        lines.append(f"  channel:   {call.option('--channel') or '(not given)'}")
        lines.append(f"  thread-ts: {call.option('--thread-ts') or '(none: new message)'}")
        text_file, inline = call.option("--text-file"), call.option("--text")
    else:
        target = pos[1] if len(pos) > 1 else "(not given)"
        lines.append(f"  task/target: {target}")
        if call.verb == "status":
            lines.append(f"  new status:  {pos[2] if len(pos) > 2 else '(not given)'}")
        text_file = call.option("--comment-file") or call.option("--text-file")
        inline = call.option("--comment") or call.option("--text")
    if text_file:
        lines.append(f"  text (from {text_file}):")
        body = read_text_file(text_file, cwd)
    elif inline:
        lines.append("  text (inline):")
        body = inline
    else:
        lines.append("  text: (none given)")
        body = ""
    if body:
        lines.append(hooklib.truncate(body, TEXT_LIMIT))
    if not call.has_flag("--confirm"):
        lines.append("  note: no --confirm, so the CLI should refuse (exit 3).")
    lines.append("Approve only if Don has approved this exact text and target.")
    return "\n".join(lines)


def describe_mcp(call: hooklib.McpCall, tool_input: Any) -> str:
    try:
        payload = json.dumps(tool_input, indent=1, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(tool_input)
    return "\n".join(
        [
            f"OUTBOUND {call.source.upper()} WRITE via MCP: {call.server} / {call.action}",
            "  payload:",
            hooklib.truncate(payload, TEXT_LIMIT),
            "Approve only if Don has approved this exact payload.",
        ]
    )


def decide(event: dict[str, Any]) -> str | None:
    """The 'ask' reason for this tool call, or None to pass silently."""
    tool_name = event.get("tool_name")
    if not isinstance(tool_name, str):
        return None
    command = hooklib.bash_command(event)
    if command is not None:
        cwd = hooklib.event_cwd(event)
        writes = [c for c in hooklib.cli_calls(command) if c.is_write]
        if not writes:
            return None
        return "\n\n".join(describe_cli(c, cwd) for c in writes)
    mcp = hooklib.classify_mcp(tool_name)
    if mcp is not None and mcp.is_write:
        return describe_mcp(mcp, event.get("tool_input"))
    return None


def main() -> int:
    event = hooklib.read_event()
    if event is None:
        return 0
    try:
        reason = decide(event)
    except Exception as exc:  # fail toward asking, never toward silence
        haystack = (
            str(event.get("tool_name", "")) + json.dumps(event.get("tool_input", ""), default=str)
        ).lower()
        if "slack" not in haystack and "clickup" not in haystack:
            return 0
        reason = f"outbound_guard could not evaluate this call ({exc!r}); review it by hand."
    if reason:
        ask(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
