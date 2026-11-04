"""Environment-driven configuration, with worktree-aware ``.env`` resolution.

Sessions in this harness work inside git worktrees, and ``.env`` is gitignored,
so ``git worktree add`` never creates one there. Walking a fixed number of
parents up from this file lands in the *worktree's* root, which has no token.
``git rev-parse --path-format=absolute --git-common-dir`` returns the primary
checkout's ``.git`` from inside any worktree, and its parent holds the shared
``.env``.

``--path-format=absolute`` matters: plain ``--git-common-dir`` returns a
relative ``.git`` from the primary checkout, which would silently break the
ordinary case while the worktree case kept working.

The token is never logged, printed, or written anywhere by this package.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

TOKEN_VAR = "SLACK_USER_TOKEN"

#: ``clients/slack`` -- the package root, home of an optional package-local ``.env``.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]

#: The repo root as reached by walking up from this file. In a worktree this is
#: the worktree's root, which is why ``_primary_checkout_root`` also exists.
_WORKING_ROOT = Path(__file__).resolve().parents[4]

_GIT_TIMEOUT_SECONDS = 5.0


class ConfigError(RuntimeError):
    """Missing or invalid local configuration."""


def _primary_checkout_root() -> Path | None:
    """The primary checkout's root, or ``None`` for every legitimate failure.

    ``git`` missing, not a repository, a bare repo, a wedged git, or a git too
    old for ``--path-format`` all mean "this candidate does not exist", not an
    error -- the caller has other candidates.
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

    path = Path(result.stdout.strip())
    # A bare repo's common dir is not named ".git" and has no working tree;
    # empty output becomes Path("."), whose name is "" -- both rejected here.
    if path.name != ".git":
        return None
    return path.parent


def _candidate_env_files() -> list[Path]:
    """Every ``.env`` searched, narrowest first, de-duplicated in order.

    ``load_dotenv(override=False)`` keeps the first value seen, so order is
    precedence: package-local beats working-root beats primary checkout.
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
    lines = [f"{TOKEN_VAR} is not set.", "", "Searched, in order:"]
    for path in searched:
        marker = "found" if path.is_file() else "not found"
        lines.append(f"  [{marker}] {path}")

    primary = _primary_checkout_root()
    if primary is not None and primary != _WORKING_ROOT:
        lines += [
            "",
            f"This is a git worktree ({_WORKING_ROOT}); the primary checkout is {primary}.",
            "`.env` is gitignored, so `git worktree add` does not create one here; "
            "the primary checkout's .env (searched above) is the shared one.",
        ]

    lines += [
        "",
        f"Add {TOKEN_VAR}=xoxp-... to the primary checkout's .env (see "
        "clients/slack/.env.example) or export it for this shell.",
        "The value is the *User OAuth Token* from your Slack app's "
        "'OAuth & Permissions' page (see clients/slack/README.md).",
    ]
    return "\n".join(lines)


def raw_cache_root() -> Path:
    """Where ``extract/`` writes raw responses: ``<repo root>/.cache/raw/slack``.

    The working root (the worktree you are in), not the primary checkout, so
    parallel sessions do not write into each other's caches.
    """
    return _WORKING_ROOT / ".cache" / "raw" / "slack"


@dataclass(frozen=True)
class SlackConfig:
    token: str = field(repr=False)
    timeout_seconds: float = 30.0
    max_retries: int = 5

    @classmethod
    def from_env(cls, *, env_file: Path | None = None) -> SlackConfig:
        """Load the token, searching every ``.env`` this layout uses.

        ``env_file``, when given, is the only file consulted, so a test naming
        a fixture never reads the developer's real credential.
        """
        searched = [env_file] if env_file is not None else _candidate_env_files()
        for path in searched:
            # override=False: an exported SLACK_USER_TOKEN beats every file.
            load_dotenv(path, override=False)

        token = os.environ.get(TOKEN_VAR, "").strip()
        if not token:
            raise ConfigError(_missing_token_message(searched))
        return cls(token=token)
