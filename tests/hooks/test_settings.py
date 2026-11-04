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
    # MED-3: writes the first list missed
    "mcp__claude_ai_Slack__slack_get_file_upload_url",
    "mcp__claude_ai_ClickUp__clickup_add_tag_to_task",
    "mcp__claude_ai_ClickUp__clickup_remove_tag_from_task",
    "mcp__claude_ai_ClickUp__clickup_add_task_dependency",
    "mcp__claude_ai_ClickUp__clickup_remove_task_dependency",
    "mcp__claude_ai_ClickUp__clickup_add_task_link",
    "mcp__claude_ai_ClickUp__clickup_remove_task_link",
    "mcp__claude_ai_ClickUp__clickup_add_task_to_list",
    "mcp__claude_ai_ClickUp__clickup_remove_task_from_list",
    "mcp__claude_ai_ClickUp__clickup_add_time_entry",
    "mcp__claude_ai_ClickUp__clickup_start_time_tracking",
    "mcp__claude_ai_ClickUp__clickup_stop_time_tracking",
    "mcp__claude_ai_ClickUp__clickup_attach_task_file",
    "mcp__claude_ai_ClickUp__clickup_request_attachment_upload",
    "mcp__claude_ai_ClickUp__clickup_create_document",
    "mcp__claude_ai_ClickUp__clickup_create_document_page",
    "mcp__claude_ai_ClickUp__clickup_update_document_page",
    "mcp__claude_ai_ClickUp__clickup_create_folder",
    "mcp__claude_ai_ClickUp__clickup_update_folder",
    "mcp__claude_ai_ClickUp__clickup_create_list",
    "mcp__claude_ai_ClickUp__clickup_create_list_in_folder",
    "mcp__claude_ai_ClickUp__clickup_update_list",
    "mcp__claude_ai_ClickUp__clickup_create_reminder",
    "mcp__claude_ai_ClickUp__clickup_update_reminder",
    "mcp__claude_ai_ClickUp__clickup_create_space_tag",
    "Bash(bin/slack send *)",
    "Bash(bin/clickup comment *)",
    "Bash(bin/clickup status *)",
]
REQUIRED_DENY = [
    "Bash(rm -rf *)",
    "Bash(git push --force*)",
    "Bash(git reset --hard *)",
    # tokens live in .env; .env.example (empty values) stays readable
    "Read(**/.env)",
    "Read(**/.env.*)",
    "Read(!.env.example)",
    # raw API calls bypass the CLIs' approved-text hash
    "Bash(curl *slack.com/api*)",
    "Bash(curl *api.clickup.com*)",
    "Bash(wget *slack.com/api*)",
    "Bash(wget *api.clickup.com*)",
]

#: Every tool the claude.ai Slack and ClickUp connectors exposed on 2026-11-04
#: (the session's deferred-tool list). The completeness test below checks that
#: each one the hook treats as a write is also in permissions.ask, and vice versa.
CONNECTOR_TOOLS = {
    "mcp__claude_ai_Slack__slack_" + t
    for t in (
        "add_list_record add_reaction complete_file_upload create_canvas create_conversation "
        "create_list get_file_upload_url get_reactions list_channel_members list_user_channels "
        "read_canvas read_channel read_file read_list read_thread read_user_profile "
        "schedule_message search_channels search_emojis search_public "
        "search_public_and_private search_users send_message send_message_draft update_canvas "
        "update_list update_list_record"
    ).split()
} | {
    "mcp__claude_ai_ClickUp__clickup_" + t
    for t in (
        "add_tag_to_task add_task_dependency add_task_link add_task_to_list add_time_entry "
        "attach_task_file create_comment create_document create_document_page create_folder "
        "create_list create_list_in_folder create_reminder create_space_tag create_task "
        "create_task_comment delete_chat_message delete_comment delete_task "
        "download_document_page_attachment download_task_attachment execute_operator "
        "filter_tasks find_member_by_name get_bulk_tasks_time_in_status get_chat_channel_messages "
        "get_chat_channels get_chat_message_replies get_current_time_entry get_custom_fields "
        "get_document_pages get_folder get_list get_operators get_schema get_task "
        "get_task_comments get_task_time_in_status get_threaded_comments get_time_entries "
        "get_user get_workspace_hierarchy get_workspace_members list_document_page_attachments "
        "list_document_pages merge_tasks move_task react_to_chat_message remove_tag_from_task "
        "remove_task_dependency remove_task_from_list remove_task_link request_attachment_upload "
        "resolve_assignees search search_reminders send_chat_message start_time_tracking "
        "stop_time_tracking update_chat_message update_comment update_document_page "
        "update_folder update_list update_reminder update_task"
    ).split()
}


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


def test_outbound_guard_blocks_when_it_fails_or_times_out():
    """A timed-out or crashed PreToolUse hook otherwise lets the call through."""
    entries = [
        hook
        for group in SETTINGS["hooks"]["PreToolUse"]
        for hook in group["hooks"]
        if hook["command"].endswith('outbound_guard.py"')
    ]
    assert len(entries) == 2
    for hook in entries:
        assert hook.get("onFailure") == "block", hook


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


def test_permissions_ask_covers_every_known_connector_write(run_hook, make_mcp_event):
    """The claim in README.md / .claude/README.md: the ask list is complete."""
    ask = set(SETTINGS["permissions"]["ask"])
    writes = set()
    for tool in sorted(CONNECTOR_TOOLS):
        out = run_hook("outbound_guard.py", make_mcp_event(tool, {"x": 1}))
        if out.stdout:
            assert out.json["hookSpecificOutput"]["permissionDecision"] == "ask", tool
            writes.add(tool)
    assert len(writes) == 51  # pinned by hand: 13 Slack + 38 ClickUp write tools
    assert writes - ask == set(), "hook-write tools missing from permissions.ask"
    mcp_asks = {r for r in ask if r.startswith("mcp__claude_ai_")}
    assert mcp_asks - writes == set(), "ask rules for tools the hook treats as reads"


def test_settings_local_is_gitignored():
    assert ".claude/settings.local.json" in (REPO / ".gitignore").read_text().splitlines()
