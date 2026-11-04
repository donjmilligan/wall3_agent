#!/usr/bin/env python3
"""PreToolUse hook on Bash: block commits that would publish secrets.

This repo is public. When a Bash command runs ``git commit`` (including
``git -C <dir> commit`` and ``cd <dir> && git commit``), inspect what the
commit would contain and exit 2 (which blocks the tool call; stderr is the
reason Claude sees) if it includes:

* a ``.env`` / ``.env.*`` file other than ``.env.example``;
* anything under a ``.cache/`` or ``exports/`` directory;
* content matching a token pattern (Slack ``xox?-`` / ``xapp-``, ClickUp
  ``pk_<n>_``, Anthropic ``sk-ant-``, GitHub ``ghp_`` / ``github_pat_``).

"What the commit would contain" is the index, plus tracked modifications when
the commit uses ``-a``, plus whatever a ``git add`` earlier in the same
command would stage (found with ``git add --dry-run``), because at PreToolUse
time that ``git add`` has not run yet. Otherwise exit 0 with no output.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hooklib  # noqa: E402

GIT_TIMEOUT = 20
MAX_SCAN_BYTES = 2 * 1024 * 1024

TOKEN_PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    ("Slack token", re.compile(rb"xox[abeprs]-[0-9A-Za-z-]{10,}")),
    ("Slack app token", re.compile(rb"xapp-\d+-[0-9A-Za-z-]{10,}")),
    ("ClickUp token", re.compile(rb"pk_\d+_[0-9A-Za-z]{16,}")),
    ("Anthropic key", re.compile(rb"sk-ant-[0-9A-Za-z_-]{16,}")),
    ("GitHub token", re.compile(rb"ghp_[0-9A-Za-z]{30,}")),
    ("GitHub token", re.compile(rb"github_pat_[0-9A-Za-z_]{30,}")),
]

#: `git commit` options that consume the following token.
_COMMIT_VALUE_OPTS = {
    "-m",
    "-F",
    "-C",
    "-c",
    "-t",
    "--message",
    "--file",
    "--author",
    "--date",
    "--cleanup",
    "--fixup",
    "--squash",
    "--reuse-message",
    "--reedit-message",
    "--template",
    "--trailer",
    "--pathspec-from-file",
}
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


@dataclass
class GitCall:
    cwd: str
    global_opts: list[str]
    sub: str
    args: list[str] = field(default_factory=list)


def parse_git(argv: list[str], cwd: str) -> GitCall | None:
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    rest = argv[1:]
    global_opts: list[str] = []
    while rest and rest[0].startswith("-"):
        opt = rest[0]
        if opt == "-C" and len(rest) > 1:
            cwd = os.path.join(cwd, rest[1])
            rest = rest[2:]
        elif opt in _GIT_VALUE_OPTS and len(rest) > 1:
            global_opts += rest[:2]
            rest = rest[2:]
        else:
            global_opts.append(opt)
            rest = rest[1:]
    if not rest:
        return None
    return GitCall(cwd, global_opts, rest[0], rest[1:])


def planned_git_calls(command: str, cwd: str) -> list[GitCall]:
    """Git calls in order, tracking `cd <dir>` between segments."""
    calls: list[GitCall] = []
    for argv in hooklib.simple_commands(command):
        if argv[0] == "cd" and len(argv) > 1:
            cwd = os.path.join(cwd, os.path.expanduser(argv[1]))
            continue
        call = parse_git(argv, cwd)
        if call is not None:
            calls.append(call)
    return calls


def commit_uses_all(args: list[str]) -> bool:
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg == "--all":
            return True
        if arg in _COMMIT_VALUE_OPTS:
            skip = True
            continue
        if arg.startswith("-") and not arg.startswith("--"):
            for ch in arg[1:]:
                if ch == "a":
                    return True
                if ch in "mFCct":
                    break
    return False


def git(call_cwd: str, global_opts: list[str], *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", call_cwd, *global_opts, *args],
        capture_output=True,
        timeout=GIT_TIMEOUT,
        check=False,
    )


def split_z(output: bytes) -> list[str]:
    return [p.decode("utf-8", "replace") for p in output.split(b"\0") if p]


@dataclass
class Candidate:
    path: str  # repo-relative
    from_index: bool


def candidates_for(commit: GitCall, adds: list[GitCall]) -> tuple[str | None, list[Candidate]]:
    top = git(commit.cwd, commit.global_opts, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None, []
    toplevel = top.stdout.decode().strip()
    found: dict[str, Candidate] = {}
    staged = git(
        commit.cwd,
        commit.global_opts,
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACMR",
        "-z",
    )
    for path in split_z(staged.stdout):
        found[path] = Candidate(path, True)
    if commit_uses_all(commit.args):
        modified = git(commit.cwd, commit.global_opts, "diff", "--name-only", "-z")
        for path in split_z(modified.stdout):
            found.setdefault(path, Candidate(path, False))
    for add in adds:
        dry = git(add.cwd, add.global_opts, "add", "--dry-run", *add.args)
        for line in dry.stdout.decode("utf-8", "replace").splitlines():
            m = re.match(r"^add '(.+)'$", line.strip())
            if not m:
                continue
            rel = os.path.relpath(os.path.join(add.cwd, m.group(1)), toplevel)
            if rel.startswith(".."):
                rel = m.group(1)
            found.setdefault(rel, Candidate(rel, False))
    return toplevel, list(found.values())


def path_problem(path: str) -> str | None:
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file (secrets); only .env.example may be committed"
    for directory in (".cache", "exports"):
        if directory in parts[:-1]:
            return f"under {directory}/ (message caches/exports are never committed)"
    return None


def content_problems(data: bytes) -> list[str]:
    if b"\0" in data[:8192]:
        return []
    problems = []
    for label, pattern in TOKEN_PATTERNS:
        m = pattern.search(data)
        if m:
            line = data.count(b"\n", 0, m.start()) + 1
            preview = m.group(0)[:8].decode("ascii", "replace")
            problems.append(f"line {line}: looks like a {label} ({preview}...)")
    return problems


def read_candidate(commit: GitCall, toplevel: str, cand: Candidate) -> bytes:
    if cand.from_index:
        shown = git(commit.cwd, commit.global_opts, "show", f":{cand.path}")
        if shown.returncode == 0:
            return shown.stdout[:MAX_SCAN_BYTES]
    try:
        with open(os.path.join(toplevel, cand.path), "rb") as fh:
            return fh.read(MAX_SCAN_BYTES)
    except OSError:
        return b""


def violations(command: str, cwd: str) -> list[str]:
    calls = planned_git_calls(command, cwd)
    out: list[str] = []
    for idx, call in enumerate(calls):
        if call.sub != "commit":
            continue
        adds = [c for c in calls[:idx] if c.sub == "add"]
        toplevel, cands = candidates_for(call, adds)
        if toplevel is None:
            continue
        for cand in cands:
            problem = path_problem(cand.path)
            if problem:
                out.append(f"{cand.path}: {problem}")
                continue
            for p in content_problems(read_candidate(call, toplevel, cand)):
                out.append(f"{cand.path}: {p}")
    return out


def main() -> int:
    event = hooklib.read_event()
    if event is None:
        return 0
    command = hooklib.bash_command(event)
    if not command or "git" not in command or "commit" not in command:
        return 0
    try:
        problems = violations(command, hooklib.event_cwd(event))
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"pre_commit_guard: could not inspect the commit ({exc}); allowing.\n")
        return 0
    if not problems:
        return 0
    sys.stderr.write(
        "pre_commit_guard: commit blocked — this repo is public "
        "(.claude/rules/secrets-and-public-repo.md).\n"
    )
    for problem in problems:
        sys.stderr.write(f"  - {problem}\n")
    sys.stderr.write(
        "Unstage with `git restore --staged <path>`, remove the secret, and commit again. "
        "Never bypass with --no-verify.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
