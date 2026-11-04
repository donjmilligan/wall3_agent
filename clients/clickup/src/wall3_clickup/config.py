"""Environment-driven configuration, resolved correctly from a git worktree.

Harness sessions work in linked worktrees. `.env` is gitignored, so
`git worktree add` never creates one there, and a client that only looks
"next to itself" cannot find the credential from the directory every session
works in. Resolution therefore searches, narrowest first:

1. `clients/clickup/.env`        -- deliberate per-tool override
2. `<working repo root>/.env`     -- the worktree's own, if someone made one
3. `<primary checkout root>/.env` -- the shared file, the normal answer

The primary checkout comes from `git rev-parse --path-format=absolute
--git-common-dir`. Plain `--git-common-dir` returns a *relative* `.git` from
the primary checkout and an absolute path from a worktree, so the naive form
works in the case someone would test and silently breaks the ordinary
checkout. `--path-format=absolute` makes both correct.

`load_dotenv(override=False)` everywhere: an exported variable beats every
file, and the first file that defines a variable wins.

The token is never logged, printed or written anywhere by this package. The
dataclass hides it from `repr()` so an accidental `print(config)` or a
traceback's locals cannot leak it.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

#: `clients/clickup/` -- this file is `clients/clickup/src/wall3_clickup/config.py`.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]

#: The repo root reached by walking up from this file. In a linked worktree
#: this is the *worktree's* root, which is exactly the case
#: `_primary_checkout_root()` exists to cover.
_WORKING_ROOT = Path(__file__).resolve().parents[4]

#: `git rev-parse` is local and returns in milliseconds; the timeout only
#: stops a wedged git from hanging every command.
_GIT_TIMEOUT_SECONDS = 5.0

TOKEN_VAR = "CLICKUP_API_TOKEN"
TEAM_VAR = "CLICKUP_TEAM_ID"


class ConfigError(RuntimeError):
    """Missing or invalid local configuration. CLI exit code 1."""


def _primary_checkout_root() -> Path | None:
    """The primary checkout's root, or None when it cannot be determined.

    None (never an exception) for: git not installed, not inside a repo, a
    bare repo, git too old for `--path-format` (< 2.31), a hung git, or a
    non-zero exit even if stdout looks plausible. Each just means this one
    candidate path does not exist.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            cwd=_PACKAGE_ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    common_dir = result.stdout.strip()
    if not common_dir:
        return None
    path = Path(common_dir)
    # A bare repo's common dir has no working tree above it.
    if path.name != ".git":
        return None
    return path.parent


def _candidate_env_files() -> list[Path]:
    """Every `.env` searched, in load order, de-duplicated (order kept).

    In an ordinary checkout the git-resolved root *is* the walked root; without
    de-duplication the error message would list the same file twice.
    """
    candidates = [_PACKAGE_ROOT / ".env", _WORKING_ROOT / ".env"]
    primary = _primary_checkout_root()
    if primary is not None:
        candidates.append(primary / ".env")
    ordered: list[Path] = []
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _missing_token_message(searched: list[Path]) -> str:
    """An error a reader can act on: every path tried, and whether it exists.

    Only-"not found" lines say "create the file"; a "found" line that did not
    supply the token says "look inside that file". Minting a new token is the
    last resort, not the first instruction -- the usual cause is a token that
    exists on disk somewhere the client was not looking.
    """
    lines = [f"{TOKEN_VAR} is not set.", "", "Searched, in order:"]
    for path in searched:
        marker = "found" if path.is_file() else "not found"
        lines.append(f"  [{marker}] {path}")
    primary = _primary_checkout_root()
    if primary is not None and primary != _WORKING_ROOT:
        lines += [
            "",
            f"This is a git worktree ({_WORKING_ROOT}); the primary checkout is {primary}.",
            "`.env` is gitignored, so `git worktree add` does not create one here -- "
            "the primary checkout's .env is the shared one, and it was searched above.",
        ]
    lines += [
        "",
        f"Add {TOKEN_VAR}=pk_... to the primary checkout's .env (see "
        f"clients/clickup/.env.example) or export it for this shell.",
        "Only mint a new token (ClickUp -> Settings -> Apps -> API Token) if you do "
        "not have one already.",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class ClickUpConfig:
    # repr=False: the token must never appear in a repr, log line or traceback.
    token: str = field(repr=False)
    team_id: str = ""
    timeout_seconds: float = 30.0
    max_retries: int = 6

    @classmethod
    def from_env(cls, *, require_token: bool, env_file: Path | None = None) -> ClickUpConfig:
        """Load configuration from the environment and the `.env` search path.

        `env_file`, when given, is the *only* file consulted -- an explicit
        override; also loading the repo's `.env` would make a test that names a
        fixture file depend on whatever credential is on the developer's disk.
        """
        searched = [env_file] if env_file is not None else _candidate_env_files()
        for path in searched:
            load_dotenv(path, override=False)
        token = os.environ.get(TOKEN_VAR, "").strip()
        if require_token and not token:
            raise ConfigError(_missing_token_message(searched))
        team_id = os.environ.get(TEAM_VAR, "").strip()
        return cls(token=token, team_id=team_id)


def raw_cache_root() -> Path:
    """Where extract/ writes raw snapshots: `<working repo root>/.cache/raw/clickup/`.

    The working root (the worktree's own root inside a worktree) rather than
    the primary checkout: a session's cache belongs to that session. The
    directory is gitignored at the repo root.
    """
    return _WORKING_ROOT / ".cache" / "raw" / "clickup"
