"""Shared helpers for the wall3_agent Claude Code hooks. Python stdlib only.

Every hook reads one JSON object from stdin (schema:
https://code.claude.com/docs/en/hooks). This module turns a Bash
``tool_input.command`` into the simple commands it would run, and classifies
calls to the Slack/ClickUp CLIs and MCP connectors as reads or writes.

Deliberately conservative: when a command is ambiguous the classifier leans
toward "this is a write", because a needless approval prompt costs Don one
keystroke and a missed one costs an unreviewed message. ``Analysis.clean`` is
False whenever the parse may have lost track of quoting; callers must not
grant exemptions on an unclean parse.
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

SUBST = "__WALL3_SUBST__"
_MAX_DEPTH = 6
_HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([^\s;&|<>()]+))")


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


@dataclass
class _Scan:
    segments: list[str] = field(default_factory=list)
    #: bodies of $(...) / backticks, each tagged with the segment index it sat in
    nested: list[tuple[int, str]] = field(default_factory=list)
    #: heredoc bodies, tagged with the segment index of their `<<` operator
    heredocs: list[tuple[int, str]] = field(default_factory=list)
    clean: bool = True


def _scan(s: str) -> _Scan:
    """Split ``s`` on shell control operators outside quotes.

    Also extracts ``$(...)`` / backtick bodies (which the shell executes) and
    heredoc bodies (consumed here so their text cannot desynchronise quoting).
    """
    out = _Scan()
    buf: list[str] = []
    quote: str | None = None  # "'", '"', or "$'" (ANSI-C: backslash escapes)
    pending: list[tuple[str, bool]] = []  # heredoc (delimiter, strip_tabs) awaiting a newline
    i = 0
    n = len(s)

    def flush() -> None:
        text = "".join(buf).strip()
        if text:
            out.segments.append(text)
        buf.clear()

    def seg_index() -> int:
        return len(out.segments)

    while i < n:
        c = s[i]
        if quote == "'":
            buf.append(c)
            if c == "'":
                quote = None
            i += 1
            continue
        if quote == "$'":
            if c == "\\" and i + 1 < n:
                buf.append(s[i : i + 2])
                i += 2
                continue
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
            out.nested.append((seg_index(), s[i + 2 : end]))
            buf.append(SUBST)
            i = end + 1
            continue
        if c == "`":
            end = s.find("`", i + 1)
            end = n if end == -1 else end
            out.nested.append((seg_index(), s[i + 1 : end]))
            buf.append(SUBST)
            i = end + 1
            continue
        if quote == '"':
            buf.append(c)
            if c == '"':
                quote = None
            i += 1
            continue
        if s.startswith("$'", i):
            quote = "$'"
            buf.append("$'")
            i += 2
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
            i += 1
            continue
        if s.startswith("<<<", i):
            buf.append("<<<")  # herestring, not a heredoc
            i += 3
            continue
        if s.startswith("<<", i):
            m = _HEREDOC.match(s, i)
            if m:
                delim = m.group(2) if m.group(2) is not None else m.group(3)
                if delim is None:
                    delim = m.group(4)
                pending.append((delim, m.group(1) == "-"))
                buf.append(s[i : m.end()])
                i = m.end()
                continue
        if c == "\n" and pending:
            owner = seg_index()
            flush()
            i += 1
            for delim, strip_tabs in pending:
                body: list[str] = []
                while i < n:
                    j = s.find("\n", i)
                    j = n if j == -1 else j
                    line = s[i:j]
                    i = j + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delim:
                        break
                    body.append(line)
                else:
                    out.clean = False  # unterminated heredoc
                out.heredocs.append((owner, "\n".join(body)))
            pending.clear()
            continue
        if c == "&" and ((i > 0 and s[i - 1] in "<>") or s.startswith("&>", i)):
            buf.append(c)  # part of a redirection: 2>&1, &>file, <&3
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
    if quote is not None or pending:
        out.clean = False
    flush()
    return out


def _tokenize(segment: str) -> tuple[list[str], bool]:
    try:
        return shlex.split(segment, posix=True), True
    except ValueError:
        return segment.split(), False


_REDIRECT = re.compile(r"^(?:\d+|&)?(?:>>|>\||>&|<&|<<<|<<-?|<>|&>>|&>|>|<)(.*)$", re.S)


def _split_redirects(
    tokens: list[str],
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """Remove redirections. Returns (tokens, [(operator, target)], [herestring bodies])."""
    kept: list[str] = []
    redirects: list[tuple[str, str]] = []
    herestrings: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = _REDIRECT.match(tok)
        if m and not tok.startswith(SUBST):
            target = m.group(1)
            op = tok[: len(tok) - len(target)]
            if not target and i + 1 < len(tokens):
                target = tokens[i + 1]
                i += 1
            if op.endswith("<<<"):
                herestrings.append(target)
            elif not op.endswith(("<<", "<<-")):
                redirects.append((op, target))
            i += 1
            continue
        kept.append(tok)
        i += 1
    return kept, redirects, herestrings


_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=(.*)$", re.S)
_KEYWORDS = {
    "{",
    "}",
    "!",
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "do",
    "done",
    "while",
    "until",
}
_DECLARERS = {"export", "declare", "typeset", "local", "readonly"}
#: Commands that treat a heredoc as data. Their heredoc bodies are text (a commit
#: message, a file being written), not commands; every other heredoc body is
#: parsed line by line as commands, since it may be feeding a shell.
HEREDOC_SINKS = {
    "cat",
    "tee",
    "git",
    "gh",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "sed",
    "wc",
    "sort",
    "uniq",
    "jq",
    "head",
    "tail",
    "diff",
    "tr",
    "cut",
    "column",
    "base64",
    "xxd",
    "od",
    "read",
    "mapfile",
}

#: wrapper -> options of that wrapper that consume the following token. After
#: the wrapper's options, the rest of argv is the command it runs.
_OPT_WRAPPERS: dict[str, set[str]] = {
    "command": set(),
    "builtin": set(),
    "exec": {"-a"},
    "nohup": set(),
    "noglob": set(),
    "nocorrect": set(),
    "time": {"-f", "--format", "-o", "--output"},
    "setsid": set(),
    "unbuffer": set(),
    "chronic": set(),
    "caffeinate": {"-t", "-w"},
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group"},
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {
        "-I",
        "-i",
        "-n",
        "-P",
        "-L",
        "-l",
        "-d",
        "-s",
        "-E",
        "-e",
        "-a",
        "-J",
        "-R",
        "-S",
        "--arg-file",
        "--delimiter",
        "--max-args",
        "--max-procs",
        "--replace",
    },
}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "fish", "ash", "busybox"}
_SHELL_VALUE_OPTS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_WATCH_VALUE_OPTS = {"-n", "--interval", "-q", "--equexit"}
_SCRIPT_VALUE_OPTS = {"-F", "-t", "-T", "-E", "-I", "-O", "-B", "-m", "--log-io", "--timing"}
_FLOCK_VALUE_OPTS = {"-w", "--timeout", "-E", "--conflict-exit-code"}
_PARALLEL_VALUE_OPTS = {
    "-j",
    "--jobs",
    "-N",
    "-n",
    "--max-args",
    "-S",
    "--sshlogin",
    "-I",
    "--delay",
    "--timeout",
    "-a",
    "--arg-file",
    "--colsep",
    "-C",
    "-d",
    "--delimiter",
    "-E",
    "--joblog",
    "--results",
    "--tmpdir",
    "--workdir",
    "--wd",
    "--env",
    "--retries",
}
_FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}


@dataclass
class _Stripped:
    argv: list[str]
    assigns: dict[str, str] = field(default_factory=dict)
    nested: list[str] = field(default_factory=list)  # command strings the shell would run
    nested_argv: list[list[str]] = field(default_factory=list)  # argv the wrapper would exec
    #: a shell (or `sh -s`) whose script comes from stdin, a heredoc or a herestring
    shell_stdin: bool = False


def _skip_opts(rest: list[str], takes_value: set[str]) -> list[str]:
    while rest and rest[0].startswith("-") and rest[0] != "-":
        if rest[0] == "--":
            return rest[1:]
        opt = rest[0].split("=", 1)[0]
        rest = rest[2:] if (opt in takes_value and "=" not in rest[0]) else rest[1:]
    return rest


def _strip_shell(argv: list[str], out: _Stripped) -> _Stripped:
    """``bash [opts] -c CMD`` / ``bash -lc CMD`` / ``bash script`` / ``bash`` (stdin)."""
    rest = argv[1:]
    if os.path.basename(argv[0]) == "busybox" and rest and rest[0] in ("sh", "ash"):
        rest = rest[1:]
    has_c = False
    reads_stdin = False
    while rest:
        tok = rest[0]
        if tok in ("--", "-"):
            rest = rest[1:]
            break
        if tok in _SHELL_VALUE_OPTS:
            rest = rest[2:]
            continue
        if tok in ("--command",):
            has_c = True
            rest = rest[1:]
            continue
        if tok.startswith("--"):
            rest = rest[1:]
            continue
        if len(tok) > 1 and tok[0] in "-+":
            if "c" in tok[1:]:
                has_c = True
            if "s" in tok[1:]:
                reads_stdin = True
            if tok[0] == "-" and tok[1:] and tok[-1] in "oO":
                rest = rest[2:]  # e.g. -eo pipefail
                continue
            rest = rest[1:]
            continue
        break
    if has_c:
        if rest:
            out.nested.append(rest[0])
        out.argv = []
        return out
    if reads_stdin or not rest:
        out.shell_stdin = True
        out.argv = [argv[0]]
        return out
    out.argv = [argv[0], *rest]  # `bash script.sh args`: a script file
    return out


def _strip_wrappers(argv: list[str]) -> _Stripped:
    """Drop env assignments and process wrappers; collect what wrappers would run."""
    out = _Stripped(argv=[])
    while argv:
        head = argv[0]
        base = os.path.basename(head)
        assign = _ASSIGNMENT.match(head)
        if assign:
            out.assigns[assign.group(1)] = assign.group(2)
            argv = argv[1:]
        elif head in _KEYWORDS:
            argv = argv[1:]
        elif head == "function" and len(argv) > 1:
            argv = argv[2:]
        elif head in _DECLARERS:
            for tok in argv[1:]:
                m = _ASSIGNMENT.match(tok)
                if m:
                    out.assigns[m.group(1)] = m.group(2)
            out.argv = argv
            return out
        elif base == "env":
            rest = argv[1:]
            split_string: str | None = None
            while rest and (rest[0].startswith("-") or _ASSIGNMENT.match(rest[0])):
                tok = rest[0]
                m = _ASSIGNMENT.match(tok)
                if m:
                    out.assigns[m.group(1)] = m.group(2)
                    rest = rest[1:]
                elif tok in ("-S", "--split-string"):
                    split_string = rest[1] if len(rest) > 1 else ""
                    rest = rest[2:]
                elif tok.startswith("--split-string="):
                    split_string = tok.split("=", 1)[1]
                    rest = rest[1:]
                elif tok.startswith("-S") and len(tok) > 2:
                    split_string = tok[2:]
                    rest = rest[1:]
                elif tok.split("=", 1)[0] in _OPT_WRAPPERS["env"] and "=" not in tok:
                    rest = rest[2:]
                else:
                    rest = rest[1:]
            if split_string is not None:
                out.nested.append(" ".join([split_string, *(shlex.quote(r) for r in rest)]))
                out.argv = []
                return out
            argv = rest
        elif base in _OPT_WRAPPERS:
            rest = _skip_opts(argv[1:], _OPT_WRAPPERS[base])
            if base == "timeout" and rest:
                rest = rest[1:]  # the duration
            if base == "command" and argv[1:2] in (["-v"], ["-V"]):
                out.argv = argv  # `command -v x` looks x up; it does not run it
                return out
            argv = rest
        elif base in _SHELLS:
            return _strip_shell(argv, out)
        elif base == "eval":
            out.nested.append(" ".join(argv[1:]))
            return out
        elif base == "watch":
            rest = _skip_opts(argv[1:], _WATCH_VALUE_OPTS)
            if rest:
                out.nested.append(" ".join(rest))
            return out
        elif base == "script":
            rest = argv[1:]
            command: str | None = None
            positional: list[str] = []
            while rest:
                tok = rest[0]
                if tok in ("-c", "--command"):
                    command = rest[1] if len(rest) > 1 else ""
                    rest = rest[2:]
                elif tok.startswith("--command="):
                    command = tok.split("=", 1)[1]
                    rest = rest[1:]
                elif tok in _SCRIPT_VALUE_OPTS:
                    rest = rest[2:]
                elif tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]:
                    after = tok[tok.index("c", 1) + 1 :]  # util-linux: -qc CMD / -qcCMD
                    command = after or (rest[1] if len(rest) > 1 else "")
                    rest = rest[1:] if after else rest[2:]
                elif tok.startswith("-") and not positional:
                    rest = rest[1:]
                else:
                    positional = rest
                    break
            if command is not None:
                out.nested.append(command)
            elif len(positional) > 1:
                out.nested_argv.append(positional[1:])  # BSD: script [file [command ...]]
            return out
        elif base == "flock":
            rest = _skip_opts(argv[1:], _FLOCK_VALUE_OPTS)
            rest = rest[1:]  # the lock file
            if rest[:1] in (["-c"], ["--command"]):
                if len(rest) > 1:
                    out.nested.append(rest[1])
                return out
            argv = rest
        elif base == "su":
            rest = argv[1:]
            for idx, tok in enumerate(rest):
                if tok in ("-c", "--command") and idx + 1 < len(rest):
                    out.nested.append(rest[idx + 1])
            return out
        elif base == "parallel":
            rest = _skip_opts(argv[1:], _PARALLEL_VALUE_OPTS)
            cmd: list[str] = []
            for tok in rest:
                if tok.startswith(":::"):
                    break
                cmd.append(tok)
            if cmd:
                out.nested.append(" ".join(cmd))
            return out
        elif base == "find":
            rest = argv[1:]
            for idx, tok in enumerate(rest):
                if tok in _FIND_EXEC:
                    inner: list[str] = []
                    for t in rest[idx + 1 :]:
                        if t in (";", "+"):
                            break
                        inner.append(t)
                    if inner:
                        out.nested_argv.append(inner)
            out.argv = argv
            return out
        else:
            break
    out.argv = argv
    return out


# ------------------------------------------------------------------ analysis

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def is_dynamic(token: str) -> bool:
    """True if the shell would compute this word at run time."""
    return "$" in token or SUBST in token or "`" in token


def expand_vars(token: str, assigns: dict[str, str]) -> str | None:
    """Expand ``$NAME`` / ``${NAME}`` from assignments visible in the command.

    Returns None if any part stays dynamic (unknown variable, substitution).
    ``~`` at the start is expanded too.
    """
    unknown = False

    def sub(m: re.Match[str]) -> str:
        nonlocal unknown
        name = m.group(1) or m.group(2)
        value = assigns.get(name)
        if value is None or is_dynamic(value):
            unknown = True
            return ""
        return value

    result = _VAR.sub(sub, token)
    if unknown or is_dynamic(result):
        return None
    return os.path.expanduser(result)


@dataclass
class Simple:
    """One simple command, as the shell would run it."""

    argv: list[str]
    tokens: list[str]  # before wrapper stripping, redirections removed
    redirects: list[tuple[str, str]]
    herestrings: list[str]
    assigns: dict[str, str]
    cwd: str | None  # None when a `cd` before it was not a literal path
    depth: int
    shell_stdin: bool = False


@dataclass
class Analysis:
    command: str
    simples: list[Simple] = field(default_factory=list)
    assigns: dict[str, str] = field(default_factory=dict)
    clean: bool = True

    def cli_calls(self) -> list[CliCall]:
        calls: list[CliCall] = []
        for simple in self.simples:
            argv = simple.argv
            if argv and is_dynamic(argv[0]):
                resolved = expand_vars(argv[0], self.assigns)
                if resolved:
                    argv = resolved.split() + argv[1:]
            call = classify_argv(argv)
            if call is not None:
                call.simple = simple
                calls.append(call)
        return calls


def analyze(command: str, cwd: str | None = None) -> Analysis:
    """Every simple command ``command`` would run, nested ones included."""
    analysis = Analysis(command)
    _analyze_into(analysis, command, cwd, 0)
    return analysis


def _analyze_into(
    analysis: Analysis, command: str, cwd: str | None, depth: int, *, taint: bool = True
) -> str | None:
    """Parse ``command`` into ``analysis``; returns the cwd after it (for `cd` tracking).

    ``taint=False`` is for heredoc lines, parsed one at a time on purpose: a
    quote that spans lines there is normal and must not mark the whole command
    unclean (the outer command was already split correctly).
    """
    if depth > _MAX_DEPTH:
        analysis.clean = False
        return cwd
    if not command:
        return cwd
    scan = _scan(command)
    if not scan.clean and taint:
        analysis.clean = False
    for index, segment in enumerate(scan.segments):
        tokens, ok = _tokenize(segment)
        if not ok and taint:
            analysis.clean = False
        tokens, redirects, herestrings = _split_redirects(tokens)
        stripped = _strip_wrappers(tokens)
        analysis.assigns.update(stripped.assigns)
        argv = stripped.argv
        if argv and argv[0] in ("cd", "pushd", "popd"):
            target = argv[1] if len(argv) > 1 else None
            literal = expand_vars(target, analysis.assigns) if target else None
            if argv[0] == "cd" and literal and literal != "-" and os.path.isabs(literal):
                cwd = os.path.normpath(literal)
            elif argv[0] == "cd" and literal and literal != "-" and cwd is not None:
                cwd = os.path.normpath(os.path.join(cwd, literal))
            else:
                cwd = None
        shell_stdin = stripped.shell_stdin
        analysis.simples.append(
            Simple(
                argv=argv,
                tokens=tokens,
                redirects=redirects,
                herestrings=herestrings,
                assigns=stripped.assigns,
                cwd=cwd,
                depth=depth,
                shell_stdin=shell_stdin,
            )
        )
        bodies = [body for owner, body in scan.nested if owner == index]
        bodies += stripped.nested
        if shell_stdin:
            bodies += herestrings
        for body in bodies:
            _analyze_into(analysis, body, cwd, depth + 1)
        for inner in stripped.nested_argv:
            _analyze_into(analysis, shlex.join(inner), cwd, depth + 1)
        sink = bool(argv) and os.path.basename(argv[0]) in HEREDOC_SINKS
        for owner, body in scan.heredocs:
            if owner == index and not sink:
                # Line by line: a heredoc may feed a shell, and one stray quote
                # in it must not hide the lines after it.
                for line in body.splitlines():
                    _analyze_into(analysis, line, cwd, depth + 1, taint=False)
    # bodies whose owning segment turned out empty (e.g. `cat <<EOF |` + newline)
    for owner, body in scan.nested:
        if owner >= len(scan.segments):
            _analyze_into(analysis, body, cwd, depth + 1)
    for owner, body in scan.heredocs:
        if owner >= len(scan.segments):
            for line in body.splitlines():
                _analyze_into(analysis, line, cwd, depth + 1, taint=False)
    return cwd


def simple_commands(command: str) -> list[list[str]]:
    """The argv of every simple command ``command`` would run (wrappers stripped)."""
    return [s.argv for s in analyze(command).simples if s.argv]


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
ALL_WRITE_VERBS = WRITE_VERBS["slack"] | WRITE_VERBS["clickup"]
_CHAT_READ = {"channels", "messages"}
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
    "--index-url",
    "--extra-index-url",
    "-i",
    "--find-links",
    "-f",
    "--from",
    "--only-group",
    "--no-group",
    "--cache-dir",
    "--config-file",
    "--color",
    "--python-preference",
    "--allow-insecure-host",
    "--index-strategy",
    "--keyring-provider",
    "--resolution",
    "--prerelease",
    "--exclude-newer",
    "--link-mode",
    "--refresh-package",
    "--reinstall-package",
    "--upgrade-package",
    "-P",
    "--no-binary-package",
    "--no-build-package",
    "--config-setting",
    "-C",
}
#: python options that take a value: either attached (-Xdev) or the next token.
_PY_VALUE_CHARS = set("cmWXQ")
_PY_LONG_VALUE = {"--check-hash-based-pycs"}


@dataclass
class CliCall:
    source: str
    program: str
    args: list[str] = field(default_factory=list)
    simple: Simple | None = None

    @property
    def positionals(self) -> list[str]:
        return [a for a in self.args if not a.startswith("-")]

    @property
    def verb(self) -> str | None:
        pos = self.positionals
        return pos[0] if pos else None

    @property
    def kind(self) -> str:
        """'write', 'read', 'dynamic' (verb computed at run time), 'unknown' or 'none'."""
        pos = self.positionals
        verb = pos[0] if pos else None
        if verb is None:
            return "none"
        if is_dynamic(verb):
            return "dynamic"
        if self.source == "clickup" and verb == "chat":
            sub = pos[1] if len(pos) > 1 else None
            if sub is None:
                return "none"
            if is_dynamic(sub):
                return "dynamic"
            if sub in _CHAT_READ:
                return "read"
            return "write" if sub in _CHAT_WRITE else "unknown"
        if verb in WRITE_VERBS[self.source]:
            return "write"
        if verb in READ_VERBS[self.source]:
            return "read"
        return "unknown"

    @property
    def is_write(self) -> bool:
        kind = self.kind
        if kind == "write":
            return True
        if kind in ("unknown", "dynamic"):
            # Unknown first word (e.g. the value of a global option): fall back to
            # "does a write verb appear anywhere?" so `slack --x y send` still asks.
            return any(p in WRITE_VERBS[self.source] for p in self.positionals)
        return False

    @property
    def is_read(self) -> bool:
        return self.verb not in (None, "whoami", "help") and not self.is_write


def _classify_python(argv: list[str]) -> CliCall | None:
    rest = argv[1:]
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--" or arg == "-":
            return None
        if arg.startswith("--"):
            i += 2 if arg in _PY_LONG_VALUE else 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            for pos, ch in enumerate(arg[1:], start=1):
                if ch in _PY_VALUE_CHARS:
                    value = arg[pos + 1 :]
                    consumed = 1
                    if not value:
                        value = rest[i + 1] if i + 1 < len(rest) else ""
                        consumed = 2
                    if ch == "m":
                        source = _MODULES.get(value.split(".")[0])
                        return CliCall(source, value, rest[i + consumed :]) if source else None
                    if ch == "c":
                        return None  # inline code: heuristics, not the classifier
                    i += consumed
                    break
            else:
                i += 1
            continue
        for module, source in _MODULES.items():
            if module in arg.replace("\\", "/").split("/"):
                return CliCall(source, arg, rest[i + 1 :])
        return None
    return None


def _uv_target(argv: list[str]) -> list[str] | None:
    """For ``uv [opts] run [opts] CMD...`` / ``uv tool run`` / ``uvx``: CMD..., else None."""
    base = os.path.basename(argv[0])
    rest = argv[1:]
    if base == "uv":
        rest = _skip_opts(rest, _UV_VALUE_OPTS)
        if rest[:1] == ["tool"]:
            rest = rest[1:]
            rest = _skip_opts(rest, _UV_VALUE_OPTS)
        if not rest or rest[0] not in ("run", "x"):
            return None
        rest = rest[1:]
    while rest and rest[0].startswith("-"):
        opt = rest[0]
        if opt in ("-m", "--module"):
            return ["python", "-m", *rest[1:]]
        if opt.startswith("--module="):
            return ["python", "-m", opt.split("=", 1)[1], *rest[1:]]
        if opt.startswith("-m") and len(opt) > 2:
            return ["python", "-m", opt[2:], *rest[1:]]
        if opt in ("--script", "--gui-script", "-s"):
            return ["python", *rest[1:]]
        if opt == "--":
            rest = rest[1:]
            break
        name = opt.split("=", 1)[0]
        rest = rest[2:] if (name in _UV_VALUE_OPTS and "=" not in opt) else rest[1:]
    if rest and re.search(r"\.pyw?$", rest[0]):
        return ["python", *rest]  # `uv run post.py` runs it with python
    return rest


def classify_argv(argv: list[str], _depth: int = 0) -> CliCall | None:
    """Recognise argv as a call to the slack or clickup CLI, however invoked."""
    if not argv or _depth > _MAX_DEPTH:
        return None
    program = argv[0]
    base = os.path.basename(program)
    if base in SOURCES:
        return CliCall(base, program, argv[1:])
    if base in ("uv", "uvx"):
        target = _uv_target(argv)
        return classify_argv(target, _depth + 1) if target else None
    if base.startswith("python"):
        return _classify_python(argv)
    if base in _SHELLS and len(argv) > 1:
        script = os.path.basename(argv[1])
        if script in SOURCES:  # `bash bin/slack send ...`
            return CliCall(script, argv[1], argv[2:])
    return None


def cli_calls(command: str) -> list[CliCall]:
    return analyze(command).cli_calls()


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
#: Tools whose name starts with a read prefix but which prepare a write.
_WRITE_OVERRIDES = {"get_file_upload_url"}
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
        if self.action.lower() in _WRITE_OVERRIDES:
            return True
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
