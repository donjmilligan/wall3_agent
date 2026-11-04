"""session_start.py: state on screen, credential NAMES only, never values, always exit 0."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HOOK = "session_start.py"
EVENT = {"session_id": "s", "hook_event_name": "SessionStart", "source": "startup"}
SECRET_A = "value-that-must-never-print-A"
SECRET_B = "value-that-must-never-print-B"
SECRET_C = "value-that-must-never-print-C"
NO_CREDS = {"SLACK_USER_TOKEN": "", "CLICKUP_API_TOKEN": "", "CLICKUP_TEAM_ID": ""}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text("x\n")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "push", "-q", "origin", "main:feature/visible-branch")
    git(repo, "checkout", "-q", "-b", "feature/local-work")
    return repo


def run(run_hook, project: Path, **env: str):
    result = run_hook(HOOK, EVENT, env={**NO_CREDS, "CLAUDE_PROJECT_DIR": str(project), **env})
    assert result.returncode == 0, result.stderr
    return result.stdout


def line_for(stdout: str, name: str) -> str:
    return next(line for line in stdout.splitlines() if line.strip().startswith(name))


def test_reports_branch_worktrees_and_remote_heads(run_hook, repo):
    out = run(run_hook, repo)
    assert "branch: feature/local-work" in out
    assert "worktrees (1):" in out
    assert "remote heads on origin (2):" in out
    assert "feature/visible-branch" in out


def test_unreachable_remote_is_reported_not_fatal(run_hook, repo, tmp_path):
    git(repo, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    out = run(run_hook, repo)
    assert "remote heads: unavailable" in out
    assert "branch: feature/local-work" in out


def test_credentials_report_names_and_sources_never_values(run_hook, repo):
    (repo / ".env").write_text(
        f"# comment\nexport CLICKUP_API_TOKEN='{SECRET_B}'\nCLICKUP_TEAM_ID=\n"
    )
    out = run(run_hook, repo, SLACK_USER_TOKEN=SECRET_A)
    assert "set (environment)" in line_for(out, "SLACK_USER_TOKEN")
    assert f".env at {repo / '.env'}" in line_for(out, "CLICKUP_API_TOKEN")
    assert "MISSING" in line_for(out, "CLICKUP_TEAM_ID")
    assert SECRET_A not in out
    assert SECRET_B not in out


def test_worktree_finds_env_in_primary_checkout(run_hook, repo, tmp_path):
    (repo / ".env").write_text(f"CLICKUP_TEAM_ID={SECRET_C}\n")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature/wt")
    out = run(run_hook, wt)
    assert "branch: feature/wt" in out
    assert "worktrees (2):" in out
    assert f".env at {repo / '.env'}" in line_for(out, "CLICKUP_TEAM_ID")
    assert SECRET_C not in out


def test_all_missing_when_nothing_configured(run_hook, repo):
    out = run(run_hook, repo)
    for name in NO_CREDS:
        assert "MISSING" in line_for(out, name)


def test_not_a_repo_and_garbage_stdin_still_exit_zero(run_hook, tmp_path):
    result = run_hook(HOOK, "{{{", env={**NO_CREDS, "CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert result.returncode == 0
    assert "wall3_agent session start" in result.stdout
    assert "branch: unknown" in result.stdout
