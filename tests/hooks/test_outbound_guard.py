"""outbound_guard.py: every Slack/ClickUp write asks; every read passes silently."""

from __future__ import annotations

from pathlib import Path

import pytest

HOOK = "outbound_guard.py"

SLACK_SEND = "--channel C1 --thread-ts 1.2 --text-file draft.txt --confirm"

WRITE_COMMANDS = [
    f"bin/slack send {SLACK_SEND}",
    f"./bin/slack send {SLACK_SEND}",
    f"/opt/repo/bin/slack send {SLACK_SEND}",
    f"slack send {SLACK_SEND}",
    "bin/slack send --channel C1 --text-file draft.txt",
    "bin/clickup comment 86abc --text-file draft.txt --confirm",
    "bin/clickup status 86abc 'in review' --comment-file draft.txt --confirm",
    "clickup comment 86abc --text-file draft.txt",
    ".venv/bin/clickup status 86abc done --comment-file draft.txt",
    f"uv run --project clients/slack slack send {SLACK_SEND}",
    "uv run --quiet --project clients/clickup clickup status 1 done --comment-file c --confirm",
    f"python -m wall3_slack send {SLACK_SEND}",
    "python3 -m wall3_clickup.cli comment 1 --text-file draft.txt",
    f"uv run python -m wall3_slack send {SLACK_SEND}",
    f"python3 clients/slack/src/wall3_slack/cli.py send {SLACK_SEND}",
    f"cd /tmp && bin/slack send {SLACK_SEND}",
    f"bin/slack inbox --json; bin/slack send {SLACK_SEND}",
    f"bin/slack inbox --json && bin/slack send {SLACK_SEND}",
    f"SLACK_USER_TOKEN=x bin/slack send {SLACK_SEND}",
    f"timeout 30 bin/slack send {SLACK_SEND}",
    f"env FOO=1 bin/slack send {SLACK_SEND}",
    f"bash -c 'bin/slack send {SLACK_SEND}'",
    "echo $(bin/clickup comment 1 --text-file draft.txt --confirm)",
    'echo "`bin/slack send --channel C1 --text-file draft.txt`"',
    "nohup bin/clickup comment 1 --text-file draft.txt &",
    "(cd clients/slack && uv run slack send --channel C1 --text-file draft.txt)",
    f"bin/slack --profile work send {SLACK_SEND}",
    f"bin/slack --verbose send {SLACK_SEND}",
    "xargs -I{} bin/slack send --channel {} --text-file draft.txt",
    "bin/clickup chat send 123 --text-file draft.txt",
    f"eval bin/slack send {SLACK_SEND}",
]

READ_OR_UNRELATED_COMMANDS = [
    "bin/slack inbox --since 24h --json",
    "slack inbox",
    "bin/slack thread C1:1.2",
    "bin/slack channel general --since 2d --limit 50",
    "bin/slack search send",
    "bin/slack search 'please send the report'",
    "bin/slack user @someone",
    "bin/slack whoami --json",
    "bin/slack normalize clients/slack/tests/fixtures/raw/inbox --json",
    "bin/clickup task 86abc --comments --json",
    "bin/clickup chat messages 123",
    "bin/clickup chat channels",
    "bin/clickup mine --status 'in progress'",
    "bin/clickup search 'status comment'",
    "grep send bin/slack",
    "grep -rn 'slack send' docs/",
    "echo 'bin/slack send --channel C1'",
    # separators inside quotes must not split the command (mutation M8 needed these)
    "echo 'done; bin/slack send --channel C1 --text-file d.txt'",
    "git commit -m 'fix && bin/clickup comment is now guarded'",
    "grep -E 'inbox|bin/slack send' docs/notes.md",
    'echo "a && bin/slack send --channel C1"',
    "git commit -m 'guard bin/slack send and clickup comment'",
    "cat bin/clickup",
    "rg 'clickup status' .claude",
    "uv run pytest -q tests/hooks/test_outbound_guard.py -k send",
    "python -m pytest -k 'slack send'",
    "sed -n 1,5p clients/slack/send.py",
    "bin/inbox-rank --json",
    "bin/check-draft draft.txt",
    "uv run --project clients/slack pytest -q",
    "slackcat send --channel C1",
    "",
]


def decision(result) -> dict:
    assert result.returncode == 0, result.stderr
    out = result.json["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    return out


@pytest.mark.parametrize("command", WRITE_COMMANDS)
def test_cli_write_verbs_ask(run_hook, make_bash_event, command):
    out = decision(run_hook(HOOK, make_bash_event(command)))
    assert out["permissionDecision"] == "ask"
    assert "OUTBOUND" in out["permissionDecisionReason"]


@pytest.mark.parametrize("command", READ_OR_UNRELATED_COMMANDS)
def test_reads_and_unrelated_commands_pass_silently(run_hook, make_bash_event, command):
    result = run_hook(HOOK, make_bash_event(command))
    assert result.returncode == 0
    assert result.stdout == ""


def test_reason_shows_target_and_text_from_file(run_hook, make_bash_event, tmp_path: Path):
    (tmp_path / "draft.txt").write_text("Thanks, shipping the fix today.\n")
    command = "bin/slack send --channel C42 --thread-ts 1700.5 --text-file draft.txt --confirm"
    out = decision(run_hook(HOOK, make_bash_event(command, cwd=tmp_path)))
    reason = out["permissionDecisionReason"]
    assert "C42" in reason
    assert "1700.5" in reason
    assert "Thanks, shipping the fix today." in reason
    assert "no --confirm" not in reason


def test_reason_for_clickup_status_shows_task_status_and_comment(
    run_hook, make_bash_event, tmp_path: Path
):
    (tmp_path / "c.md").write_text("Moving to review: tests green.")
    command = "bin/clickup status 86abc 'in review' --comment-file c.md"
    reason = decision(run_hook(HOOK, make_bash_event(command, cwd=tmp_path)))[
        "permissionDecisionReason"
    ]
    assert "86abc" in reason
    assert "in review" in reason
    assert "Moving to review: tests green." in reason
    assert "no --confirm" in reason


def test_reason_truncates_long_text(run_hook, make_bash_event, tmp_path: Path):
    (tmp_path / "long.txt").write_text("x" * 5000 + "TAIL_MARKER")
    reason = decision(
        run_hook(HOOK, make_bash_event("bin/clickup comment 1 --text-file long.txt", tmp_path))
    )["permissionDecisionReason"]
    assert "truncated" in reason
    assert "TAIL_MARKER" not in reason
    assert "x" * 1000 in reason


def test_reason_reports_unreadable_file(run_hook, make_bash_event, tmp_path: Path):
    reason = decision(
        run_hook(
            HOOK, make_bash_event("bin/slack send --channel C1 --text-file nope.txt", tmp_path)
        )
    )["permissionDecisionReason"]
    assert "could not read nope.txt" in reason


def test_reason_shows_inline_text(run_hook, make_bash_event):
    reason = decision(
        run_hook(HOOK, make_bash_event("bin/slack send --channel C1 --text='hello there'"))
    )["permissionDecisionReason"]
    assert "hello there" in reason


MCP_WRITES = [
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
    "mcp__claude_ai_Slack__slack_send_message_draft",
    "mcp__claude_ai_Slack__slack_complete_file_upload",
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
    "mcp__claude_ai_ClickUp__clickup_start_time_tracking",
    "mcp__claude_ai_ClickUp__clickup_stop_time_tracking",
    "mcp__claude_ai_ClickUp__clickup_attach_task_file",
    "mcp__claude_ai_ClickUp__clickup_add_tag_to_task",
    "mcp__claude_ai_ClickUp__clickup_remove_tag_from_task",
    "mcp__claude_ai_ClickUp__clickup_add_task_to_list",
    "mcp__claude_ai_ClickUp__clickup_remove_task_from_list",
    "mcp__claude_ai_ClickUp__clickup_react_to_chat_message",
    "mcp__plugin_product-management_clickup__clickup_create_task",
]

MCP_READS = [
    "mcp__claude_ai_Slack__slack_read_channel",
    "mcp__claude_ai_Slack__slack_read_thread",
    "mcp__claude_ai_Slack__slack_search_public",
    "mcp__claude_ai_Slack__slack_search_users",
    "mcp__claude_ai_Slack__slack_read_user_profile",
    "mcp__claude_ai_Slack__slack_list_channel_members",
    "mcp__claude_ai_Slack__slack_get_reactions",
    "mcp__claude_ai_ClickUp__clickup_get_task",
    "mcp__claude_ai_ClickUp__clickup_get_task_comments",
    "mcp__claude_ai_ClickUp__clickup_search",
    "mcp__claude_ai_ClickUp__clickup_filter_tasks",
    "mcp__claude_ai_ClickUp__clickup_get_chat_channel_messages",
    "mcp__claude_ai_ClickUp__clickup_find_member_by_name",
    "mcp__claude_ai_ClickUp__clickup_resolve_assignees",
    "mcp__plugin_small-business_slack__authenticate",
]

UNRELATED_TOOLS = [
    "mcp__claude_ai_Figma__use_figma",
    "mcp__claude_ai_Microsoft_365__teams_send_chat_message",
    "mcp__zapier__post_to_slack",
    "Write",
    "Read",
]


@pytest.mark.parametrize("tool", MCP_WRITES)
def test_mcp_writes_ask_and_show_payload(run_hook, make_mcp_event, tool):
    payload = {"channel_id": "C1", "text": "Draft reply body"}
    out = decision(run_hook(HOOK, make_mcp_event(tool, payload)))
    assert out["permissionDecision"] == "ask"
    assert "Draft reply body" in out["permissionDecisionReason"]


@pytest.mark.parametrize("tool", MCP_READS + UNRELATED_TOOLS)
def test_mcp_reads_and_unrelated_tools_pass_silently(run_hook, make_mcp_event, tool):
    result = run_hook(HOOK, make_mcp_event(tool, {"query": "send it"}))
    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    "stdin", ["", "not json", "[1, 2]", '{"tool_name": 5}', '{"tool_name": "Bash"}']
)
def test_malformed_input_passes_without_crashing(run_hook, stdin):
    result = run_hook(HOOK, stdin)
    assert result.returncode == 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
