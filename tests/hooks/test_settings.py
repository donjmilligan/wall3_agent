""".claude/settings.json wires every hook and lists every known outbound tool.

Matchers are evaluated as Claude Code does (https://code.claude.com/docs/en/hooks):
a value of only letters/digits/_/-/|/, is an exact name list; anything else is
an unanchored regex. Python's `re.search` agrees with JS for these patterns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETTINGS = json.loads((REPO / ".claude" / "settings.json").read_text())

# Pinned from the brief: the outbound tools Don wants a prompt for, always.
REQUIRED_ASK = [
    "mcp__claude_ai_Slack__slack_send_message",
    "mcp__claude_ai_Slack__slack_schedule_message",
    "mcp__claude_ai_Slack__slack_add_reaction",
    "mcp__claude_ai_Slack__slack_create_canvas",
    "mcp__claude_ai_Slack__slack_update_canvas",
    "mcp__claude_ai_Slack__slack_create_conversation",
    "mcp__claude_ai_Slack__slack_add_list_record",
    "mcp__claude_ai_Slack__slack_update_list",
    "mcp__claude_ai_Slack__slack_update_list_record",
    "mcp__claude_ai_Slack__slack_create_list",
    "mcp__claude_ai_ClickUp__clickup_create_task_comment",
    "mcp__claude_ai_ClickUp__clickup_create_comment",
    "mcp__claude_ai_ClickUp__clickup_send_chat_message",
    "mcp__claude_ai_ClickUp__clickup_update_chat_message",
    "mcp__claude_ai_ClickUp__clickup_update_task",
    "mcp__claude_ai_ClickUp__clickup_create_task",
    "mcp__claude_ai_ClickUp__clickup_delete_task",
    "mcp__claude_ai_ClickUp__clickup_delete_comment",
    "mcp__claude_ai_ClickUp__clickup_update_comment",
    "mcp__claude_ai_ClickUp__clickup_move_task",
    "mcp__claude_ai_ClickUp__clickup_merge_tasks",
    "mcp__claude_ai_ClickUp__clickup_execute_operator",
    "Bash(bin/slack send *)",
    "Bash(bin/clickup comment *)",
    "Bash(bin/clickup status *)",
]
REQUIRED_DENY = ["Bash(rm -rf *)", "Bash(git push --force*)", "Bash(git reset --hard *)"]


def matches(matcher: str | None, tool: str) -> bool:
    if matcher in (None, "", "*"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_\-|, ]+", matcher):
        return tool in re.split(r"[|,]\s*", matcher)
    return re.search(matcher, tool) is not None


def scripts_for(event: str, tool: str = "") -> list[str]:
    out = []
    for group in SETTINGS["hooks"].get(event, []):
        if matches(group.get("matcher"), tool):
            for hook in group["hooks"]:
                assert hook["type"] == "command"
                assert '"${CLAUDE_PROJECT_DIR}/.claude/hooks/' in hook["command"]
                out.append(hook["command"].rsplit("/", 1)[1].rstrip('"'))
    return out


@pytest.mark.parametrize(
    ("event", "tool", "expected"),
    [
        ("PreToolUse", "Bash", ["outbound_guard.py", "pre_commit_guard.py"]),
        ("PreToolUse", "mcp__claude_ai_Slack__slack_send_message", ["outbound_guard.py"]),
        ("PreToolUse", "mcp__claude_ai_ClickUp__clickup_update_task", ["outbound_guard.py"]),
        ("PreToolUse", "mcp__plugin_x_clickup__clickup_create_task", ["outbound_guard.py"]),
        ("PreToolUse", "Write", []),
        ("PreToolUse", "mcp__claude_ai_Figma__use_figma", []),
        ("PreToolUse", "BashOutput", []),
        ("PostToolUse", "Bash", ["untrusted_content.py"]),
        ("PostToolUse", "mcp__claude_ai_Slack__slack_read_thread", ["untrusted_content.py"]),
        ("PostToolUse", "Read", []),
        ("SessionStart", "startup", ["session_start.py"]),
        ("Stop", "", ["notify.py"]),
    ],
)
def test_hook_wiring(event, tool, expected):
    assert scripts_for(event, tool) == expected


def test_every_wired_script_exists():
    for groups in SETTINGS["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                name = hook["command"].rsplit("/", 1)[1].rstrip('"')
                assert (REPO / ".claude" / "hooks" / name).is_file(), name


@pytest.mark.parametrize("rule", REQUIRED_ASK)
def test_required_ask_rules_present(rule):
    assert rule in SETTINGS["permissions"]["ask"]


@pytest.mark.parametrize("rule", REQUIRED_DENY)
def test_required_deny_rules_present(rule):
    assert rule in SETTINGS["permissions"]["deny"]


def test_no_mcp_rule_has_parentheses():
    # Claude Code skips any mcp__ rule with parentheses (permissions docs).
    for rule in SETTINGS["permissions"]["ask"] + SETTINGS["permissions"]["deny"]:
        if rule.startswith("mcp__"):
            assert "(" not in rule, rule


def test_every_mcp_ask_rule_is_also_a_write_to_the_hook(run_hook, make_mcp_event):
    """The two layers agree: nothing in permissions.ask is a 'read' to outbound_guard."""
    mcp_rules = [r for r in SETTINGS["permissions"]["ask"] if r.startswith("mcp__")]
    assert len(mcp_rules) >= 22
    for rule in mcp_rules:
        result = run_hook("outbound_guard.py", make_mcp_event(rule, {"text": "x"}))
        assert result.json["hookSpecificOutput"]["permissionDecision"] == "ask", rule


def test_settings_local_is_gitignored():
    assert ".claude/settings.local.json" in (REPO / ".gitignore").read_text().splitlines()
