from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wall3_clickup import config as config_module
from wall3_clickup.config import ClickUpConfig, ConfigError


def test_missing_token_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="CLICKUP_API_TOKEN"):
        ClickUpConfig.from_env(require_token=True, env_file=tmp_path / "no-such.env")


def test_token_and_team_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_abc")
    monkeypatch.setenv("CLICKUP_TEAM_ID", "9000001")

    config = ClickUpConfig.from_env(require_token=True, env_file=tmp_path / "no-such.env")

    assert (config.token, config.team_id) == ("pk_abc", "9000001")


def test_token_and_team_from_an_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CLICKUP_API_TOKEN=pk_from_file\nCLICKUP_TEAM_ID=9000002\n")

    config = ClickUpConfig.from_env(require_token=True, env_file=env)

    assert (config.token, config.team_id) == ("pk_from_file", "9000002")


def test_missing_token_is_allowed_when_not_required(tmp_path):
    config = ClickUpConfig.from_env(require_token=False, env_file=tmp_path / "no-such.env")

    assert config.token == ""


def test_raw_cache_root_is_under_the_working_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "_WORKING_ROOT", tmp_path)

    assert config_module.raw_cache_root() == tmp_path / ".cache" / "raw" / "clickup"


def test_module_roots_match_the_real_layout():
    """The two walked roots must mean what the docstring says in *this* repo."""
    here = Path(config_module.__file__).resolve()
    assert config_module._PACKAGE_ROOT == here.parents[2]
    assert (config_module._PACKAGE_ROOT / "pyproject.toml").is_file()
    assert (config_module._WORKING_ROOT / "clients" / "clickup").is_dir()


# --------------------------------------------------------------------------
# Worktree-aware resolution, against real git repos and real worktrees.
# Mocking subprocess here would measure the mock; the defect this guards
# against only shows up where real `git rev-parse` output is consumed.
# --------------------------------------------------------------------------

needs_git = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is not available on PATH",
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A primary checkout shaped like this repo: <root>/clients/clickup/src/wall3_clickup."""
    root = tmp_path / "primary"
    pkg = root / "clients" / "clickup" / "src" / "wall3_clickup"
    pkg.mkdir(parents=True)
    (pkg / "config.py").write_text("# stand-in\n")
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", "initial", cwd=root)
    # Created after the commit and never added -- like the real gitignored .env.
    (root / ".env").write_text("CLICKUP_API_TOKEN=pk_from_primary_checkout\n")
    return root


@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Path:
    path = tmp_path / "wt" / "some-branch"
    _git("worktree", "add", "-q", "-b", "feature/x", str(path), "main", cwd=repo)
    return path


def _point_module_at(monkeypatch, root: Path) -> None:
    """Repoint where the module thinks it lives; git still runs for real."""
    monkeypatch.setattr(config_module, "_PACKAGE_ROOT", root / "clients" / "clickup")
    monkeypatch.setattr(config_module, "_WORKING_ROOT", root)


@needs_git
@pytest.mark.real_env_search
def test_the_worktree_has_no_env_of_its_own(repo, worktree):
    """Premise of the next test, asserted: otherwise it could pass for the wrong reason."""
    assert (repo / ".env").is_file()
    assert not (worktree / ".env").exists()


@needs_git
@pytest.mark.real_env_search
def test_token_resolves_from_inside_a_worktree(monkeypatch, repo, worktree):
    _point_module_at(monkeypatch, worktree)

    assert ClickUpConfig.from_env(require_token=True).token == "pk_from_primary_checkout"


@needs_git
@pytest.mark.real_env_search
def test_token_resolves_from_the_primary_checkout(monkeypatch, repo):
    """Plain --git-common-dir returns a relative `.git` here; --path-format=absolute fixes it."""
    _point_module_at(monkeypatch, repo)

    assert ClickUpConfig.from_env(require_token=True).token == "pk_from_primary_checkout"


@needs_git
@pytest.mark.real_env_search
def test_primary_root_is_the_repo_not_the_worktree(monkeypatch, repo, worktree):
    """--git-common-dir, not --git-dir (whose parent is the worktree root)."""
    _point_module_at(monkeypatch, worktree)

    assert config_module._primary_checkout_root() == repo


@needs_git
@pytest.mark.real_env_search
def test_primary_root_is_absolute_from_the_primary_checkout(monkeypatch, repo):
    """From a checkout subdirectory plain --git-common-dir prints `../../.git`.

    The token test above still passes under that bug (the walked root holds
    the same .env), so this pins the root itself.
    """
    _point_module_at(monkeypatch, repo)

    root = config_module._primary_checkout_root()

    assert root == repo
    assert root is not None and root.is_absolute()


@needs_git
@pytest.mark.real_env_search
def test_package_local_env_wins_over_the_shared_one(monkeypatch, repo, worktree):
    pkg = worktree / "clients" / "clickup"
    (pkg / ".env").write_text("CLICKUP_API_TOKEN=pk_package_local\n")
    _point_module_at(monkeypatch, worktree)

    assert ClickUpConfig.from_env(require_token=True).token == "pk_package_local"


@needs_git
@pytest.mark.real_env_search
def test_an_exported_token_beats_every_file(monkeypatch, repo, worktree):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_exported")
    _point_module_at(monkeypatch, worktree)

    assert ClickUpConfig.from_env(require_token=True).token == "pk_exported"


@needs_git
@pytest.mark.real_env_search
def test_worktree_search_order_lists_all_three_paths(monkeypatch, repo, worktree):
    _point_module_at(monkeypatch, worktree)

    assert config_module._candidate_env_files() == [
        worktree / "clients" / "clickup" / ".env",
        worktree / ".env",
        repo / ".env",
    ]


@needs_git
@pytest.mark.real_env_search
def test_checkout_search_lists_each_path_once(monkeypatch, repo):
    _point_module_at(monkeypatch, repo)

    assert config_module._candidate_env_files() == [
        repo / "clients" / "clickup" / ".env",
        repo / ".env",
    ]


@needs_git
@pytest.mark.real_env_search
def test_error_lists_searched_paths_with_found_markers(monkeypatch, repo, worktree):
    (repo / ".env").write_text("SOMETHING_ELSE=1\n")  # exists, but holds no token
    _point_module_at(monkeypatch, worktree)

    with pytest.raises(ConfigError) as excinfo:
        ClickUpConfig.from_env(require_token=True)

    message = str(excinfo.value)
    assert "Searched, in order:" in message
    assert f"[found] {repo / '.env'}" in message
    assert f"[not found] {worktree / '.env'}" in message
    assert "git worktree" in message
    assert message.index("Searched, in order:") < message.index("Only mint a new token")


@needs_git
@pytest.mark.real_env_search
def test_error_does_not_mention_worktrees_in_a_plain_checkout(monkeypatch, repo):
    (repo / ".env").unlink()
    _point_module_at(monkeypatch, repo)

    with pytest.raises(ConfigError) as excinfo:
        ClickUpConfig.from_env(require_token=True)

    assert "git worktree" not in str(excinfo.value)
    assert f"[not found] {repo / '.env'}" in str(excinfo.value)


@pytest.mark.real_env_search
def test_outside_a_repository_there_is_no_primary_root(monkeypatch, tmp_path):
    outside = tmp_path / "not-a-repo" / "clients" / "clickup"
    outside.mkdir(parents=True)
    monkeypatch.setattr(config_module, "_PACKAGE_ROOT", outside)

    assert config_module._primary_checkout_root() is None


def _fake_run(result=None, exc: Exception | None = None):
    def run(*args, **kwargs):
        if exc is not None:
            raise exc
        return result

    return run


@pytest.mark.parametrize(
    "fake",
    [
        _fake_run(exc=FileNotFoundError("git")),
        _fake_run(exc=subprocess.TimeoutExpired(cmd="git", timeout=5.0)),
        _fake_run(subprocess.CompletedProcess([], 0, stdout="\n", stderr="")),
        _fake_run(subprocess.CompletedProcess([], 0, stdout="/tmp/bare.git\n", stderr="")),
        # Non-zero exit with plausible stdout: the exit code is checked on its own.
        _fake_run(subprocess.CompletedProcess([], 128, stdout="/tmp/other/.git\n", stderr="x")),
    ],
    ids=["no-git", "hung-git", "empty-output", "bare-repo", "nonzero-exit"],
)
def test_every_git_failure_mode_degrades_to_none(monkeypatch, fake):
    monkeypatch.setattr(config_module.subprocess, "run", fake)

    assert config_module._primary_checkout_root() is None


def test_a_valid_git_answer_is_used(monkeypatch):
    """Positive pair for the degradation cases above."""
    ok = subprocess.CompletedProcess([], 0, stdout="/tmp/somewhere/.git\n", stderr="")
    monkeypatch.setattr(config_module.subprocess, "run", _fake_run(ok))

    assert config_module._primary_checkout_root() == Path("/tmp/somewhere")
