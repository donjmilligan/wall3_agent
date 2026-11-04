"""Credential resolution, exercised against real git repositories and worktrees.

Mocking ``subprocess.run`` for the happy paths would measure the mock; the
defect this design prevents only shows up where ``git`` really runs. The
degradation cases (git missing, hung, lying) do patch it, because those are
the cases a real repo cannot produce on demand.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wall3_slack import config as config_module
from wall3_slack.config import ConfigError, SlackConfig

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is not available on PATH",
)

VAR = "SLACK_USER_TOKEN"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A primary checkout laid out like this repo: <root>/clients/slack/src/wall3_slack/."""
    root = tmp_path / "primary"
    pkg = root / "clients" / "slack" / "src" / "wall3_slack"
    pkg.mkdir(parents=True)
    (pkg / "config.py").write_text("# stand-in\n")
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", "initial", cwd=root)
    # Created after the commit and never added: the reason worktrees lack it.
    (root / ".env").write_text(f"{VAR}=xoxp-from-primary\n")
    return root


@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Path:
    path = tmp_path / "wt" / "some-branch"
    _git("worktree", "add", "-q", "-b", "feature/x", str(path), "main", cwd=repo)
    return path


def _point_module_at(monkeypatch, working_root: Path) -> None:
    monkeypatch.setattr(config_module, "_PACKAGE_ROOT", working_root / "clients" / "slack")
    monkeypatch.setattr(config_module, "_WORKING_ROOT", working_root)


def test_token_resolves_from_inside_a_worktree(monkeypatch, repo, worktree):
    _point_module_at(monkeypatch, worktree)

    assert SlackConfig.from_env().token == "xoxp-from-primary"


def test_the_worktree_has_no_env_of_its_own(repo, worktree):
    """Premise of the test above, asserted: otherwise it could pass for another reason."""
    assert (repo / ".env").is_file()
    assert not (worktree / ".env").exists()


def test_resolution_works_from_the_primary_checkout(monkeypatch, repo):
    """Plain --git-common-dir returns a relative '.git' here; --path-format=absolute fixes it."""
    _point_module_at(monkeypatch, repo)

    assert SlackConfig.from_env().token == "xoxp-from-primary"
    assert config_module._primary_checkout_root() == repo


def test_primary_root_is_the_repo_not_the_worktree(monkeypatch, repo, worktree):
    """--git-common-dir, not --git-dir (which would name the worktree's private dir)."""
    _point_module_at(monkeypatch, worktree)

    assert config_module._primary_checkout_root() == repo


def test_package_local_env_beats_the_shared_one(monkeypatch, repo, worktree):
    pkg = worktree / "clients" / "slack"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / ".env").write_text(f"{VAR}=xoxp-package-local\n")
    _point_module_at(monkeypatch, worktree)

    assert SlackConfig.from_env().token == "xoxp-package-local"


def test_working_root_env_beats_the_primary_checkout(monkeypatch, repo, worktree):
    (worktree / ".env").write_text(f"{VAR}=xoxp-worktree-root\n")
    _point_module_at(monkeypatch, worktree)

    assert SlackConfig.from_env().token == "xoxp-worktree-root"


def test_an_exported_token_beats_every_file(monkeypatch, repo, worktree):
    monkeypatch.setenv(VAR, "xoxp-exported")
    _point_module_at(monkeypatch, worktree)

    assert SlackConfig.from_env().token == "xoxp-exported"


def test_worktree_candidates_are_ordered_and_complete(monkeypatch, repo, worktree):
    _point_module_at(monkeypatch, worktree)

    assert config_module._candidate_env_files() == [
        worktree / "clients" / "slack" / ".env",
        worktree / ".env",
        repo / ".env",
    ]


def test_checkout_candidates_list_each_path_once(monkeypatch, repo):
    _point_module_at(monkeypatch, repo)

    assert config_module._candidate_env_files() == [
        repo / "clients" / "slack" / ".env",
        repo / ".env",
    ]


def test_missing_token_error_lists_searched_paths_and_names_the_worktree(
    monkeypatch, repo, worktree
):
    (repo / ".env").unlink()
    _point_module_at(monkeypatch, worktree)

    with pytest.raises(ConfigError) as excinfo:
        SlackConfig.from_env()

    message = str(excinfo.value)
    assert "Searched, in order:" in message
    assert f"[not found] {repo / '.env'}" in message
    assert f"[not found] {worktree / '.env'}" in message
    assert "git worktree" in message
    assert "User OAuth Token" in message


def test_missing_token_error_in_a_plain_checkout_does_not_mention_worktrees(monkeypatch, repo):
    (repo / ".env").unlink()
    _point_module_at(monkeypatch, repo)

    with pytest.raises(ConfigError) as excinfo:
        SlackConfig.from_env()

    assert "Searched, in order:" in str(excinfo.value)
    assert "git worktree" not in str(excinfo.value)


def test_found_file_without_the_var_is_marked_found(monkeypatch, repo):
    (repo / ".env").write_text("OTHER=1\n")
    _point_module_at(monkeypatch, repo)

    with pytest.raises(ConfigError, match=r"\[found\] .*\.env"):
        SlackConfig.from_env()


def test_explicit_env_file_is_the_only_file_consulted(monkeypatch, repo, tmp_path):
    _point_module_at(monkeypatch, repo)

    with pytest.raises(ConfigError) as excinfo:
        SlackConfig.from_env(env_file=tmp_path / "no-such.env")

    assert str(tmp_path / "no-such.env") in str(excinfo.value)
    assert str(repo / ".env") not in str(excinfo.value)


def test_token_is_not_in_the_config_repr(monkeypatch, repo):
    _point_module_at(monkeypatch, repo)

    config = SlackConfig.from_env()

    assert config.token == "xoxp-from-primary"
    assert "xoxp-from-primary" not in repr(config)


# ------------------------------------------------------------- degradation


def test_outside_a_repository_returns_none(monkeypatch, tmp_path):
    outside = tmp_path / "not-a-repo" / "clients" / "slack"
    outside.mkdir(parents=True)
    monkeypatch.setattr(config_module, "_PACKAGE_ROOT", outside)

    assert config_module._primary_checkout_root() is None


@pytest.mark.parametrize(
    "fake",
    [
        pytest.param(FileNotFoundError("git"), id="git-missing"),
        pytest.param(subprocess.TimeoutExpired(cmd="git", timeout=5.0), id="git-hangs"),
    ],
)
def test_git_failures_are_not_errors(monkeypatch, repo, fake):
    def _raise(*args, **kwargs):
        raise fake

    monkeypatch.setattr(config_module.subprocess, "run", _raise)

    assert config_module._primary_checkout_root() is None


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        pytest.param(0, "/somewhere/bare.git\n", id="bare-repo"),
        pytest.param(0, "\n", id="empty-output"),
        pytest.param(128, "/elsewhere/.git\n", id="nonzero-exit-with-plausible-stdout"),
    ],
)
def test_untrustworthy_git_output_is_rejected(monkeypatch, repo, returncode, stdout):
    def _fake(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(config_module.subprocess, "run", _fake)

    assert config_module._primary_checkout_root() is None
