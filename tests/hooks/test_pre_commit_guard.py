"""pre_commit_guard.py: commits carrying secrets or message exports are blocked (exit 2).

Token-shaped strings are assembled at runtime so this file never contains one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HOOK = "pre_commit_guard.py"

FAKE_TOKENS = {
    "Slack token": "xo" + "xb-" + "1234567890-abcdefghijklmn",
    "Slack app token": "xa" + "pp-1-" + "A1B2C3D4E5F6G7H8",
    "ClickUp token": "pk" + "_123456_" + "ABCDEFGHIJKLMNOPQRSTUV12",
    "Anthropic key": "sk-" + "ant-" + "api03-" + "x" * 24,
    "GitHub token": "gh" + "p_" + "a" * 36,
    "GitHub PAT": "github" + "_pat_" + "11ABC" + "x" * 30,
    "AWS access key id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def stage(repo: Path, rel: str, content: str = "x\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "-f", rel)


def commit(run_hook, make_bash_event, repo: Path, command: str = "git commit -m wip", cwd=None):
    return run_hook(HOOK, make_bash_event(command, cwd=cwd or repo))


def assert_blocked(result, *needles: str) -> None:
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "commit blocked" in result.stderr
    for needle in needles:
        assert needle in result.stderr


def assert_allowed(result) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "commit blocked" not in result.stderr


@pytest.mark.parametrize("rel", [".env", ".env.local", "clients/slack/.env", ".env.production"])
def test_env_files_block(run_hook, make_bash_event, repo, rel):
    stage(repo, rel, "SLACK_USER_TOKEN=\n")
    assert_blocked(commit(run_hook, make_bash_event, repo), rel, "environment file")


def test_env_example_is_allowed(run_hook, make_bash_event, repo):
    stage(repo, ".env.example", "SLACK_USER_TOKEN=\n")
    assert_allowed(commit(run_hook, make_bash_event, repo))


@pytest.mark.parametrize(
    "rel", [".cache/inbox.json", "exports/2026-11-04.jsonl", "clients/slack/.cache/users.json"]
)
def test_cache_and_exports_block(run_hook, make_bash_event, repo, rel):
    stage(repo, rel)
    assert_blocked(commit(run_hook, make_bash_event, repo), rel)


def test_similar_names_outside_those_dirs_are_allowed(run_hook, make_bash_event, repo):
    stage(repo, "docs/exports.md")
    stage(repo, "src/cache/x.py")
    stage(repo, "notes.env.example.md")
    assert_allowed(commit(run_hook, make_bash_event, repo))


@pytest.mark.parametrize("label", sorted(FAKE_TOKENS))
def test_token_content_blocks_without_echoing_the_token(run_hook, make_bash_event, repo, label):
    token = FAKE_TOKENS[label]
    stage(repo, "src/config.py", f"# setup\nTOKEN = '{token}'\n")
    result = commit(run_hook, make_bash_event, repo)
    assert_blocked(result, "src/config.py", "line 2")
    assert token not in result.stderr
    assert token[:8] in result.stderr


def test_clean_staged_file_is_allowed(run_hook, make_bash_event, repo):
    stage(repo, "src/config.py", "TOKEN = os.environ['SLACK_USER_TOKEN']\n")
    assert_allowed(commit(run_hook, make_bash_event, repo))


def test_index_content_is_scanned_not_working_tree(run_hook, make_bash_event, repo):
    stage(repo, "a.txt", FAKE_TOKENS["Slack token"] + "\n")
    (repo / "a.txt").write_text("cleaned in working tree but not re-staged\n")
    assert_blocked(commit(run_hook, make_bash_event, repo), "a.txt")


def test_unstaged_secret_without_dash_a_is_allowed(run_hook, make_bash_event, repo):
    (repo / "README.md").write_text(FAKE_TOKENS["GitHub token"] + "\n")
    assert_allowed(commit(run_hook, make_bash_event, repo))


@pytest.mark.parametrize("command", ["git commit -am wip", "git commit --all -m wip"])
def test_commit_all_scans_tracked_modifications(run_hook, make_bash_event, repo, command):
    (repo / "README.md").write_text(FAKE_TOKENS["GitHub token"] + "\n")
    assert_blocked(commit(run_hook, make_bash_event, repo, command), "README.md")


def test_git_add_earlier_in_same_command_is_considered(run_hook, make_bash_event, repo):
    (repo / ".env").write_text("CLICKUP_API_TOKEN=\n")
    assert_blocked(
        commit(run_hook, make_bash_event, repo, "git add .env && git commit -m wip"), ".env"
    )


def test_git_add_all_respects_gitignore(run_hook, make_bash_event, repo):
    (repo / ".gitignore").write_text(".env\n")
    (repo / ".env").write_text("CLICKUP_API_TOKEN=\n")
    (repo / "ok.txt").write_text("fine\n")
    assert_allowed(commit(run_hook, make_bash_event, repo, "git add -A && git commit -m wip"))
    (repo / ".gitignore").write_text("")
    assert_blocked(
        commit(run_hook, make_bash_event, repo, "git add -A && git commit -m wip"), ".env"
    )


def test_git_dash_c_from_another_directory(run_hook, make_bash_event, repo, tmp_path):
    stage(repo, ".env")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = commit(run_hook, make_bash_event, repo, f"git -C {repo} commit -m wip", elsewhere)
    assert_blocked(result, ".env")


def test_cd_then_commit(run_hook, make_bash_event, repo, tmp_path):
    stage(repo, "exports/x.jsonl")
    result = commit(run_hook, make_bash_event, repo, f"cd {repo} && git commit -m wip", tmp_path)
    assert_blocked(result, "exports/x.jsonl")


@pytest.mark.parametrize(
    "command",
    ["git status", "git diff --cached", "echo 'git commit -m x'", "git log --grep commit"],
)
def test_non_commit_commands_are_ignored_even_with_bad_staging(
    run_hook, make_bash_event, repo, command
):
    stage(repo, ".env")
    assert_allowed(commit(run_hook, make_bash_event, repo, command))


def test_commit_message_text_is_not_scanned(run_hook, make_bash_event, repo):
    stage(repo, ".env.example")
    assert_allowed(commit(run_hook, make_bash_event, repo, "git commit -m 'add .env.example'"))


def test_outside_a_repository_is_allowed(run_hook, make_bash_event, tmp_path):
    assert_allowed(run_hook(HOOK, make_bash_event("git commit -m wip", cwd=tmp_path)))


@pytest.mark.parametrize("stdin", ["", "garbage", "[]", '{"tool_name": "Bash"}'])
def test_malformed_input_is_allowed(run_hook, stdin):
    assert_allowed(run_hook(HOOK, stdin))
