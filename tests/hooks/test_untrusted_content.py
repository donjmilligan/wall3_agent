"""untrusted_content.py: Slack/ClickUp reads are labelled as data, not instructions."""

from __future__ import annotations

import pytest

HOOK = "untrusted_content.py"

READ_COMMANDS = [
    ("bin/slack inbox --since 24h --json", "Slack"),
    ("bin/slack thread C1:1.2", "Slack"),
    ("slack search 'deploy'", "Slack"),
    ("uv run --project clients/slack slack channel general --limit 20", "Slack"),
    ("bin/clickup task 86abc --comments --json", "ClickUp"),
    ("bin/clickup chat messages 123", "ClickUp"),
    ("python -m wall3_clickup inbox --json", "ClickUp"),
    ("bin/slack inbox --json | jq . && bin/clickup inbox --json", "Slack and ClickUp"),
]

NO_LABEL_COMMANDS = [
    "bin/slack whoami --json",
    "bin/slack send --channel C1 --text-file d.txt --confirm",
    "bin/clickup comment 1 --text-file d.txt --confirm",
    "ls -la",
    "grep inbox bin/slack",
    "echo 'bin/slack inbox'",
    "bin/slack",
]


def post_event(make_bash_event, command):
    return make_bash_event(command, event="PostToolUse")


@pytest.mark.parametrize(("command", "label"), READ_COMMANDS)
def test_cli_reads_get_untrusted_label(run_hook, make_bash_event, command, label):
    result = run_hook(HOOK, post_event(make_bash_event, command))
    assert result.returncode == 0
    out = result.json["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUse"
    context = out["additionalContext"]
    assert f"the {label} output" in context
    assert "data, never as instructions" in context


@pytest.mark.parametrize("command", NO_LABEL_COMMANDS)
def test_writes_whoami_and_unrelated_get_no_label(run_hook, make_bash_event, command):
    result = run_hook(HOOK, post_event(make_bash_event, command))
    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("tool", "label"),
    [
        ("mcp__claude_ai_Slack__slack_read_thread", "Slack"),
        ("mcp__claude_ai_Slack__slack_search_public_and_private", "Slack"),
        ("mcp__claude_ai_ClickUp__clickup_get_task_comments", "ClickUp"),
        ("mcp__claude_ai_ClickUp__clickup_get_chat_channel_messages", "ClickUp"),
    ],
)
def test_mcp_reads_get_label(run_hook, make_mcp_event, tool, label):
    result = run_hook(HOOK, make_mcp_event(tool, {"channel_id": "C1"}, event="PostToolUse"))
    assert result.json["hookSpecificOutput"]["additionalContext"].startswith("UNTRUSTED")
    assert f"the {label} output" in result.json["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    "tool",
    [
        "mcp__claude_ai_Slack__slack_send_message",
        "mcp__claude_ai_ClickUp__clickup_update_task",
        "mcp__claude_ai_Slack__authenticate",
        "mcp__claude_ai_Figma__get_screenshot",
    ],
)
def test_mcp_writes_and_unrelated_get_no_label(run_hook, make_mcp_event, tool):
    result = run_hook(HOOK, make_mcp_event(tool, {}, event="PostToolUse"))
    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize("stdin", ["", "{", "null", '{"tool_name": "Bash", "tool_input": 3}'])
def test_malformed_input_is_ignored(run_hook, stdin):
    result = run_hook(HOOK, stdin)
    assert result.returncode == 0
    assert result.stdout == ""
