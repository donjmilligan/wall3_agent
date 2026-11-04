"""Shared helpers for the wall3_agent Claude Code hooks. Python stdlib only.

Every hook reads one JSON object from stdin (schema:
https://code.claude.com/docs/en/hooks). This module turns a Bash
``tool_input.command`` into the simple commands it would run, and classifies
calls to the Slack/ClickUp CLIs and MCP connectors as reads or writes.

Deliberately conservative: when a command is ambiguous the classifier leans
toward "this is a write", because a needless approval prompt costs Don one
keystroke and a missed one costs an unreviewed message.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- io


def read_event() -> dict[str, Any] | None:
    """Parse the hook's stdin JSON. Returns None on anything malformed."""
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def event_cwd(event: dict[str, Any]) -> str:
    """Directory the tool call runs in: the event's cwd, else the project root."""
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def bash_command(event: dict[str, Any]) -> str | None:
    """The command string of a Bash tool call, or None for anything else."""
    if event.get("tool_name") != "Bash":
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    return command if isinstance(command, str) else None


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.write("\n")


def truncate(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


# ----------------------------------------------------------- shell splitting

_SUBST = "__WALL3_SUBST__"
_MAX_DEPTH = 4


def _match_paren(s: str, start: int) -> int:
    """Index of the ')' closing a '$(' whose body starts at ``start``."""
    depth = 1
    i = start
    quote: str | None = None
    while i < len(s):
        c = s[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"':
                i += 1
        elif c in "'\"":
            quote = c
        elif c == "\\":
            i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(s)


def _scan(s: str) -> tuple[list[str], list[str]]:
    """Split ``s`` on shell control operators outside quotes.

    Returns (segments, nested): the simple-command strings, plus the bodies of
    any ``$(...)`` or backtick substitutions (which the shell also executes).
    """
    segments: list[str] = []
    nested: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(s)

    def flush() -> None:
        text = "".join(buf).strip()
        if text:
            segments.append(text)
        buf.clear()

    while i < n:
        c = s[i]
        if quote == "'":
            buf.append(c)
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(s[i : i + 2])
            i += 2
            continue
        if s.startswith("$(", i):
            end = _match_paren(s, i + 2)
            nested.append(s[i + 2 : end])
            buf.append(_SUBST)
            i = end + 1
            continue
        if c == "`":
            end = s.find("`", i + 1)
            end = n if end == -1 else end
            nested.append(s[i + 1 : end])
            buf.append(_SUBST)
            i = end + 1
            continue
        if quote == '"':
            buf.append(c)
            if c == '"':
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            i += 1
            continue
        if s.startswith(("&&", "||", "|&"), i):
            flush()
            i += 2
            continue
        if c in ";|&\n()":
            flush()
            i += 1
            continue
        buf.append(c)
        i += 1
    flush()
    return segments, nested


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_KEYWORDS = {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until"}
_PLAIN_WRAPPERS = {"command", "builtin", "exec", "nohup", "noglob", "time", "sudo"}
# wrapper -> options of that wrapper that consume the following token
_OPT_WRAPPERS: dict[str, set[str]] = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a", "--arg-file", "--delimiter"},
}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}


def _strip_wrappers(argv: list[str]) -> tuple[list[str], list[str]]:
    """Drop env assignments and process wrappers. Returns (argv, nested commands)."""
    nested: list[str] = []
    while argv:
        head = argv[0]
        base = os.path.basename(head)
        if _ASSIGNMENT.match(head) or head in _KEYWORDS:
            argv = argv[1:]
        elif base in _PLAIN_WRAPPERS:
            argv = argv[1:]
        elif base in _OPT_WRAPPERS:
            takes_value = _OPT_WRAPPERS[base]
            rest = argv[1:]
            while rest and (rest[0].startswith("-") or _ASSIGNMENT.match(rest[0])):
                rest = rest[2:] if rest[0] in takes_value else rest[1:]
            if base == "timeout" and rest:
                rest = rest[1:]  # the duration
            argv = rest
        elif base in _SHELLS and "-c" in argv[1:]:
            idx = argv.index("-c")
            if idx + 1 < len(argv):
                nested.append(argv[idx + 1])
            return [], nested
        elif base == "eval":
            nested.append(" ".join(argv[1:]))
            return [], nested
        else:
            break
    return argv, nested


def simple_commands(command: str, _depth: int = 0) -> list[list[str]]:
    """Every simple command (argv) ``command`` would run, nested ones included."""
    if _depth > _MAX_DEPTH or not command:
        return []
    out: list[list[str]] = []
    segments, nested = _scan(command)
    for segment in segments:
        argv, more = _strip_wrappers(_tokenize(segment))
        nested.extend(more)
        if argv:
            out.append(argv)
    for body in nested:
        out.extend(simple_commands(body, _depth + 1))
    return out


# ---------------------------------------------------------- CLI classification

SOURCES = ("slack", "clickup")
_MODULES = {"wall3_slack": "slack", "wall3_clickup": "clickup"}

#: Verbs that only read. Anything listed in WRITE_VERBS asks for approval.
READ_VERBS: dict[str, set[str]] = {
    "slack": {"whoami", "inbox", "thread", "channel", "search", "user", "normalize", "help"},
    "clickup": {"whoami", "inbox", "task", "search", "mine", "chat", "normalize", "help"},
}
#: Verbs that write to Slack/ClickUp. The contract's are ``slack send`` and
#: ``clickup comment|status``; the rest are guarded pre-emptively so a new
#: write verb is never silently unguarded.
WRITE_VERBS: dict[str, set[str]] = {
    "slack": {"send", "post", "reply", "react", "schedule", "update", "edit", "delete", "upload"},
    "clickup": {
        "comment",
        "status",
        "create",
        "update",
        "edit",
        "delete",
        "assign",
        "move",
        "attach",
        "tag",
        "untag",
    },
}
_CHAT_WRITE = {"send", "post", "reply", "update", "edit", "delete", "react"}

_UV_VALUE_OPTS = {
    "--project",
    "--directory",
    "--with",
    "--with-editable",
    "--with-requirements",
    "--python",
    "-p",
    "--package",
    "--group",
    "--extra",
    "--env-file",
    "--index",
    "--default-index",
    "--from",
    "--only-group",
    "--no-group",
}


@dataclass
class CliCall:
    source: str
    program: str
    args: list[str] = field(default_factory=list)

    @property
    def positionals(self) -> list[str]:
        return [a for a in self.args if not a.startswith("-")]

    @property
    def verb(self) -> str | None:
        pos = self.positionals
        return pos[0] if pos else None

    @property
    def is_write(self) -> bool:
        verb = self.verb
        if verb is None:
            return False
        if self.source == "clickup" and verb == "chat":
            pos = self.positionals
            return len(pos) > 1 and pos[1] in _CHAT_WRITE
        if verb in WRITE_VERBS[self.source]:
            return True
        if verb in READ_VERBS[self.source]:
            return False
        # Unknown first word (e.g. the value of a global option): fall back to
        # "does a write verb appear anywhere?" so `slack --x y send` still asks.
        return any(p in WRITE_VERBS[self.source] for p in self.positionals)

    @property
    def is_read(self) -> bool:
        return self.verb not in (None, "whoami", "help") and not self.is_write

    def option(self, name: str) -> str | None:
        for i, arg in enumerate(self.args):
            if arg == name and i + 1 < len(self.args):
                return self.args[i + 1]
            if arg.startswith(name + "="):
                return arg[len(name) + 1 :]
        return None

    def has_flag(self, name: str) -> bool:
        return any(a == name or a.startswith(name + "=") for a in self.args)


def classify_argv(argv: list[str], _depth: int = 0) -> CliCall | None:
    """Recognise argv as a call to the slack or clickup CLI, however invoked."""
    if not argv or _depth > _MAX_DEPTH:
        return None
    program = argv[0]
    base = os.path.basename(program)
    if base in SOURCES:
        return CliCall(base, program, argv[1:])
    if base in ("uv", "uvx"):
        rest = argv[1:]
        if base == "uv":
            if not rest or rest[0] != "run":
                return None
            rest = rest[1:]
        while rest and rest[0].startswith("-"):
            opt = rest[0]
            rest = rest[2:] if opt in _UV_VALUE_OPTS else rest[1:]
        return classify_argv(rest, _depth + 1)
    if base.startswith("python"):
        rest = argv[1:]
        for i, arg in enumerate(rest):
            if arg == "-m" and i + 1 < len(rest):
                source = _MODULES.get(rest[i + 1].split(".")[0])
                return CliCall(source, rest[i + 1], rest[i + 2 :]) if source else None
            if arg.startswith("-m") and len(arg) > 2:
                source = _MODULES.get(arg[2:].split(".")[0])
                return CliCall(source, arg[2:], rest[i + 1 :]) if source else None
            if not arg.startswith("-"):
                for module, source in _MODULES.items():
                    if module in arg.replace("\\", "/").split("/"):
                        return CliCall(source, arg, rest[i + 1 :])
                return None
    return None


def cli_calls(command: str) -> list[CliCall]:
    calls = []
    for argv in simple_commands(command):
        call = classify_argv(argv)
        if call is not None:
            calls.append(call)
    return calls


# ---------------------------------------------------------- MCP classification

_SOURCE_PATTERNS = {"slack": re.compile(r"[Ss]lack"), "clickup": re.compile(r"[Cc]lick[Uu]p")}
_READ_PREFIXES = {
    "get",
    "read",
    "search",
    "list",
    "find",
    "filter",
    "resolve",
    "download",
    "fetch",
    "lookup",
    "whoami",
}
_NON_CONTENT = {"authenticate", "complete_authentication"}


@dataclass
class McpCall:
    source: str
    server: str
    action: str

    @property
    def is_write(self) -> bool:
        if self.action in _NON_CONTENT:
            return False
        return self.action.split("_")[0].lower() not in _READ_PREFIXES

    @property
    def is_read(self) -> bool:
        return not self.is_write and self.action not in _NON_CONTENT


def classify_mcp(tool_name: str) -> McpCall | None:
    """``mcp__<server>__<tool>`` where the server names Slack or ClickUp."""
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    if len(parts) < 3:
        return None
    server, tool = parts[1], "__".join(parts[2:])
    for source, pattern in _SOURCE_PATTERNS.items():
        if pattern.search(server):
            action = re.sub(rf"^{source}_", "", tool, flags=re.IGNORECASE)
            return McpCall(source, server, action)
    return None
