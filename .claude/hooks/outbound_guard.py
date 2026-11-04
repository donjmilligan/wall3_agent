#!/usr/bin/env python3
"""PreToolUse hook: no Slack/ClickUp write runs without Don's approval.

The safety goal: every write reaches Don as a permission prompt showing the
exact target and exact text, and what is sent is exactly what was shown. The
model types ``--confirm``, so ``--confirm`` is not a second key; the CLIs also
require ``--expect-sha256`` (the hash of the approved text file), and this hook
shows Don the text together with whether the file hashes to that value.

Decisions (``hookSpecificOutput.permissionDecision``):

* **deny** -- a recognised CLI write verb whose preview could be wrong: a
  repeated or unknown option, a value computed at run time, process
  substitution, the text file also named elsewhere in the command, a text file
  that is missing / not a regular file / too large, ``--confirm`` without
  ``--expect-sha256``, or a file whose sha256 differs from ``--expect-sha256``.
* **ask** -- a recognised write verb that passed those checks (the reason shows
  target, full text, sha256 and whether it matches); a CLI call whose verb is
  computed at run time or unknown; every non-read Slack/ClickUp MCP tool; and
  the fallback heuristics below, each with a reason saying why.
* silent -- everything else (exit 0, no output), so normal permission rules apply.

Fallback heuristics (ask) catch writes that do not go through the CLI's verb
parser: raw API hosts, token variable names, ``.env``, the CLIs' Python
packages, a renamed or copied CLI, and interpreters/shells running code the
hook cannot read. Exactly what is and is not caught: .claude/hooks/README.md.

The hook never blocks on I/O: the text file is ``stat``-ed first and only a
regular file is opened (non-blocking), so a FIFO cannot stall it past its
timeout. If evaluating any call raises, it asks; a guard that crashes open is
no guard. ``onFailure: "block"`` in .claude/settings.json covers a timeout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hooklib  # noqa: E402

TEXT_LIMIT = 4000
FILE_READ_LIMIT = 64 * 1024
GIT_TIMEOUT = 3

# ------------------------------------------------------------------ output


def respond(decision: str, reason: str) -> None:
    hooklib.emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }
    )


@dataclass
class Verdict:
    denies: list[str] = field(default_factory=list)
    asks: list[str] = field(default_factory=list)

    def result(self) -> tuple[str, str] | None:
        if self.denies:
            return "deny", "\n\n".join(self.denies + self.asks)
        if self.asks:
            return "ask", "\n\n".join(self.asks)
        return None


def render_text(text: str) -> tuple[str, int]:
    """Text as it will be shown in the prompt, with invisible characters escaped.

    Control and format characters (ANSI escapes, bidi overrides, zero-width
    characters) could make the prompt show something other than what is sent.
    """
    out: list[str] = []
    hidden = 0
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
        elif unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp"):
            out.append(f"\\u{ord(ch):04x}")
            hidden += 1
        else:
            out.append(ch)
    return "".join(out), hidden


# ------------------------------------------------------------ CLI write verbs


@dataclass(frozen=True)
class VerbSpec:
    value_opts: frozenset[str]
    flags: frozenset[str]
    positionals: int  # after the verb
    text_opt: str
    required: frozenset[str]
    labels: tuple[str, ...] = ()  # names for the positionals, in order


_SPECS: dict[tuple[str, str], VerbSpec] = {
    ("slack", "send"): VerbSpec(
        value_opts=frozenset({"--channel", "--thread-ts", "--text-file", "--expect-sha256"}),
        flags=frozenset({"--confirm", "--help", "-h"}),
        positionals=0,
        text_opt="--text-file",
        required=frozenset({"--channel", "--text-file"}),
    ),
    ("clickup", "comment"): VerbSpec(
        value_opts=frozenset({"--text-file", "--expect-sha256"}),
        flags=frozenset({"--confirm", "--json", "--help", "-h"}),
        positionals=1,
        text_opt="--text-file",
        required=frozenset({"--text-file"}),
        labels=("task",),
    ),
    ("clickup", "status"): VerbSpec(
        value_opts=frozenset({"--comment-file", "--expect-sha256"}),
        flags=frozenset({"--confirm", "--json", "--help", "-h"}),
        positionals=2,
        text_opt="--comment-file",
        required=frozenset({"--comment-file"}),
        labels=("task", "new status"),
    ),
}
_GUARDED_OPTS = {
    "--channel",
    "--thread-ts",
    "--text-file",
    "--comment-file",
    "--expect-sha256",
    "--confirm",
}
_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_GLOB = re.compile(r"[*?\[]")


@dataclass
class ParsedWrite:
    values: dict[str, list[str]] = field(default_factory=dict)
    flags: dict[str, int] = field(default_factory=dict)
    positionals: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def parse_write(spec: VerbSpec, args: list[str]) -> ParsedWrite:
    """Parse the arguments after the verb exactly as strictly as the CLI does, or stricter."""
    parsed = ParsedWrite()
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            parsed.problems.append("`--` (end of options) is not allowed in a write")
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            name, eq, value = tok.partition("=")
            if name in spec.value_opts:
                if not eq:
                    if i + 1 >= len(args):
                        parsed.problems.append(f"{name} has no value")
                        i += 1
                        continue
                    value = args[i + 1]
                    i += 1
                parsed.values.setdefault(name, []).append(value)
            elif name in spec.flags and not eq:
                parsed.flags[name] = parsed.flags.get(name, 0) + 1
            else:
                parsed.problems.append(
                    f"unknown option {tok!r} (the CLI accepts no abbreviations or extra options)"
                )
            i += 1
            continue
        parsed.positionals.append(tok)
        i += 1
    for name, values in parsed.values.items():
        if len(values) > 1 and name in _GUARDED_OPTS:
            parsed.problems.append(
                f"{name} is given {len(values)} times ({', '.join(values)}); the preview "
                "and the CLI could disagree on which one is used"
            )
    if parsed.flags.get("--confirm", 0) > 1:
        parsed.problems.append("--confirm is given more than once")
    for name in sorted(spec.required - parsed.values.keys()):
        parsed.problems.append(f"{name} is missing, so there is nothing to preview")
    if len(parsed.positionals) != spec.positionals:
        parsed.problems.append(
            f"expected {spec.positionals} positional argument(s) "
            f"({', '.join(spec.labels) or 'none'}), got {parsed.positionals}"
        )
    return parsed


@dataclass
class TextFile:
    path: str
    data: bytes
    sha256: str


def load_text_file(path: str) -> tuple[TextFile | None, str | None]:
    """Read a text file for the preview without ever blocking.

    ``os.stat`` first: anything but a regular file (FIFO, device, directory,
    socket) is refused without being opened. Then a non-blocking open, an
    ``fstat`` on the descriptor (in case it was swapped), and a capped read.
    """
    try:
        info = os.stat(path)
    except OSError as exc:
        return None, f"cannot stat the text file {path}: {exc.strerror or exc}"
    if not stat.S_ISREG(info.st_mode):
        return None, f"the text file {path} is not a regular file (FIFO, device or directory)"
    if info.st_size > FILE_READ_LIMIT:
        return None, f"the text file {path} is {info.st_size} bytes (limit {FILE_READ_LIMIT})"
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return None, f"cannot open the text file {path}: {exc.strerror or exc}"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, f"the text file {path} is not a regular file"
        data = os.read(fd, FILE_READ_LIMIT + 1)
    except OSError as exc:
        return None, f"cannot read the text file {path}: {exc.strerror or exc}"
    finally:
        os.close(fd)
    if len(data) > FILE_READ_LIMIT:
        return None, f"the text file {path} is larger than {FILE_READ_LIMIT} bytes"
    return TextFile(path, data, hashlib.sha256(data).hexdigest()), None


def _occurrences(needle: str, haystack: str) -> int:
    if not needle:
        return 0
    return len(re.findall(r"(?<![\w.-])" + re.escape(needle) + r"(?![\w.-])", haystack))


def check_write(
    call: hooklib.CliCall, analysis: hooklib.Analysis, event_cwd: str
) -> tuple[str, str]:
    """('deny' | 'ask', reason) for a recognised write verb."""
    verb = call.verb or ""
    head = f"OUTBOUND {call.source.upper()} WRITE: `{call.program} {verb}`"
    spec = _SPECS.get((call.source, verb))
    if spec is None:
        return "ask", "\n".join(
            [
                head,
                f"  `{verb}` is not a write verb of the {call.source} CLI contract; the CLI "
                "should reject it. Approve only if you know what this does.",
                f"  command: {analysis.command[:TEXT_LIMIT]}",
            ]
        )
    args = call.args[call.args.index(verb) + 1 :] if verb in call.args else call.args
    parsed = parse_write(spec, args)
    problems = list(parsed.problems)

    if "<(" in analysis.command or ">(" in analysis.command:
        problems.append("process substitution `<(...)` / `>(...)` in a write command")
    literal_values = [v for vs in parsed.values.values() for v in vs] + parsed.positionals
    for value in literal_values:
        if hooklib.is_dynamic(value) and not (
            value in parsed.values.get(spec.text_opt, [])
            and hooklib.expand_vars(value, analysis.assigns)
        ):
            problems.append(f"{value!r} is computed at run time, so the preview cannot show it")
        elif _GLOB.search(value):
            problems.append(f"{value!r} contains a glob character")

    confirm = parsed.flags.get("--confirm", 0) > 0
    expect_values = parsed.values.get("--expect-sha256", [])
    expect = expect_values[0] if expect_values else None
    if confirm and expect is None:
        problems.append(
            "--confirm without --expect-sha256: run the dry run first, show Don the "
            "preview, then confirm with the sha256 it printed"
        )
    if expect is not None and not _SHA256_HEX.match(expect):
        problems.append(f"--expect-sha256 {expect!r} is not 64 hex characters")

    text: TextFile | None = None
    text_values = parsed.values.get(spec.text_opt, [])
    raw_path = text_values[0] if len(text_values) == 1 else None
    if raw_path is not None:
        path = hooklib.expand_vars(raw_path, analysis.assigns)
        cwd = call.simple.cwd if call.simple is not None else event_cwd
        if path in (None, "", "-") or (path or "").startswith(("/dev/", "/proc/")):
            problems.append(f"{spec.text_opt} {raw_path!r} is not a literal file the hook can read")
        elif not os.path.isabs(path or "") and cwd is None:
            problems.append(
                f"{spec.text_opt} {raw_path!r} is relative, and a `cd` earlier in the command "
                "is not literal, so the hook cannot tell which file it is"
            )
        else:
            full = os.path.join(cwd or event_cwd, path or "")
            base = os.path.basename(raw_path.rstrip("/"))
            if _occurrences(base, analysis.command) > 1:
                problems.append(
                    f"the text file name {base!r} appears elsewhere in this command; a write "
                    "must not run alongside anything that could change its text file"
                )
            text, problem = load_text_file(full)
            if problem:
                problems.append(problem)
            elif _DOTENV.search(os.path.basename(os.path.realpath(full))):
                problems.append(f"the text file {raw_path!r} is (or links to) a .env file")

    lines = [head]
    if call.source == "slack":
        lines.append(f"  channel:   {(parsed.values.get('--channel') or ['(not given)'])[0]}")
        thread = (parsed.values.get("--thread-ts") or ["(none: new message)"])[0]
        lines.append(f"  thread-ts: {thread}")
    else:
        for label, value in zip(spec.labels, parsed.positionals, strict=False):
            lines.append(f"  {label + ':':<12} {value}")
            if label == "task":
                lines.append(f"  {'':<12} https://app.clickup.com/t/{value}")
    if text is not None:
        lines.append(f"  text file: {text.path} ({len(text.data)} bytes)")
        lines.append(f"  sha256:    {text.sha256}")
        if expect is not None and _SHA256_HEX.match(expect):
            if expect.lower() == text.sha256:
                lines.append("  --expect-sha256: MATCHES the file (the CLI will send these bytes)")
            else:
                problems.append(
                    f"sha256 mismatch: the file hashes to {text.sha256} but --expect-sha256 "
                    f"is {expect.lower()}; the text changed after it was previewed"
                )
    if confirm:
        lines.append("  mode:      SEND (--confirm): this posts as Don")
    else:
        lines.append(
            "  mode:      DRY RUN (no --confirm): the CLI prints the preview and sha256, "
            "exits 3, and sends nothing"
        )

    if problems:
        return "deny", "\n".join(
            [
                f"BLOCKED {head}",
                *(f"  - {p}" for p in problems),
                "Fix the command: one literal value per option, the text in its own file, "
                "the write run on its own, and --confirm only with the --expect-sha256 "
                "printed by the dry run.",
            ]
        )

    assert text is not None
    shown, hidden = render_text(text.data.decode("utf-8", errors="replace"))
    lines.append("  ----- text -----")
    lines.append(hooklib.truncate(shown, TEXT_LIMIT))
    lines.append("  ----- end -----")
    if len(shown) > TEXT_LIMIT:
        lines.append(
            f"  WARNING: the text is {len(shown)} chars and only the first {TEXT_LIMIT} are "
            "shown; read the whole file before approving."
        )
    if hidden:
        lines.append(f"  WARNING: {hidden} invisible/control character(s) shown as \\uXXXX.")
    lines.append("Approve only if Don has approved this exact text and target.")
    return "ask", "\n".join(lines)


# --------------------------------------------------------- fallback heuristics

#: Raw API hosts (not permalinks: `<team>.slack.com/archives/...` and
#: `app.clickup.com/t/...` are how reads are addressed).
_API_HOST = re.compile(
    r"(?i)(?:slack\.com/api\b|hooks\.slack\.com|slack\.com/services\b|files\.slack\.com"
    r"|wss-[a-z0-9-]*\.slack\.com|api\.clickup\.com|app\.clickup\.com/api\b)"
)
_TOKEN_NAMES = re.compile(r"SLACK_[A-Z_]*TOKEN|CLICKUP_[A-Z_]*TOKEN")
_TOKEN_SHAPES = re.compile(r"xox[abeprs]-[0-9A-Za-z-]{6,}|xapp-\d|pk_\d+_[0-9A-Za-z]{6,}")
_DOTENV = re.compile(r"(?<![\w.-])\.env(?:\.(?!example\b)[\w.-]+)?(?![\w.-])")
_PACKAGES = re.compile(r"wall3_(?:slack|clickup)")
_CLIENT_DIRS = re.compile(r"clients/(?:slack|clickup)(?![\w-])")
_CLI_NAME = re.compile(r"(?:^|[\s/=,{:'\"])(?:slack|clickup)(?=$|[\s,}'\";|&)])")
_CLI_SHAPED = {"--expect-sha256", "--text-file", "--comment-file"}

#: Programs that only read or print their arguments: a marker that appears only
#: in these (on a clean parse, with no executor in the command) is not a write.
_INSPECTORS = {
    "ls",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "wc",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "ack",
    "git",
    "echo",
    "printf",
    "sed",
    "jq",
    "diff",
    "file",
    "stat",
    "du",
    "find",
    "tree",
    "realpath",
    "readlink",
    "basename",
    "dirname",
    "which",
    "type",
    "test",
    "[",
    "true",
    "false",
    ":",
    "cd",
    "pwd",
    "mkdir",
    "touch",
    "mktemp",
    "sort",
    "uniq",
    "cut",
    "tr",
    "column",
    "date",
    "sleep",
    "pytest",
    "ruff",
    "pyright",
    "mypy",
    "check-draft",
    "inbox-rank",
    "gh",
}
_DEV_TOOLS = {"pytest", "ruff", "pyright", "mypy"}
_COPIERS = {"cp", "ln", "install", "rsync", "ditto", "mv", "scp", "alias", "link"}
_MAKES = {"make", "gmake", "just"}
_ALWAYS_INTERPRETERS = {"osascript"}
_INLINE_INTERPRETERS = {
    "perl": {"-e", "-E"},
    "ruby": {"-e"},
    "node": {"-e", "-p", "--eval", "--print"},
    "deno": {"eval"},
    "bun": {"-e", "--eval"},
    "php": {"-r"},
    "lua": {"-e"},
    "Rscript": {"-e"},
    "tclsh": set(),
    "expect": {"-c"},
    "swift": set(),
}


def _program(argv: list[str]) -> str:
    return os.path.basename(argv[0]) if argv else ""


def _is_inspector(argv: list[str]) -> bool:
    if _program(argv) == "git" and ("-c" in argv[1:] or "config" in argv[1:]):
        return False  # `git -c alias.x='!cmd'` / `git config alias...` can run commands
    return _program(argv) in _INSPECTORS or _dev_tool(argv) is not None


def _dev_tool(argv: list[str]) -> list[str] | None:
    """The dev-tool argv (pytest/ruff/...) if argv runs one via uv/python/directly."""
    base = _program(argv)
    if base in _DEV_TOOLS:
        return argv
    if base in ("uv", "uvx"):
        target = hooklib._uv_target(argv)  # noqa: SLF001 - same package
        return _dev_tool(target) if target else None
    if base.startswith("python") and len(argv) > 2 and argv[1] == "-m" and argv[2] in _DEV_TOOLS:
        return argv[2:]
    return None


def _project_root() -> str:
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _reviewed_file(path: str, cwd: str | None) -> bool:
    """True for a file whose code is in the repo as reviewed: tracked and unmodified,
    or (for tests) any file under a `tests/` directory inside the project."""
    if cwd is None:
        return False
    full = os.path.realpath(os.path.join(cwd, os.path.expanduser(path)))
    root = _project_root()
    if full.startswith(root + os.sep) and "tests" in full[len(root) :].split(os.sep):
        return True
    try:
        tracked = subprocess.run(
            ["git", "-C", cwd, "ls-files", "--error-unmatch", "--", full],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
        if tracked.returncode != 0:
            return False
        clean = subprocess.run(
            ["git", "-C", cwd, "diff", "--quiet", "HEAD", "--", full],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
        return clean.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _script_args(argv: list[str]) -> list[str]:
    """Positional arguments that look like script files (.py/.sh/... or any path)."""
    return [a for a in argv[1:] if not a.startswith("-") and re.search(r"\.(py|pyw)$", a)]


def executors(simple: hooklib.Simple) -> list[str]:
    """Why this simple command runs code the hook cannot read (empty if it doesn't)."""
    argv = simple.argv
    base = _program(argv)
    if not argv:
        return []
    if simple.shell_stdin:
        return [f"`{base}` reads its script from stdin, a heredoc or a herestring"]
    if base in hooklib._SHELLS and len(argv) > 1:  # noqa: SLF001
        return [f"`{base} {argv[1]}` runs a script file"]
    if base in ("source", "."):
        return [f"`{base} {' '.join(argv[1:2])}` runs a script file in the shell"]
    if base in _MAKES:
        return [f"`{base}` runs recipes the hook cannot see"]
    if base in _ALWAYS_INTERPRETERS:
        return [f"`{base}` runs a script (it can drive other apps)"]
    if base in _INLINE_INTERPRETERS:
        inline = _INLINE_INTERPRETERS[base]
        if any(a in inline or any(a.startswith(f + "=") for f in inline) for a in argv[1:]):
            return [f"`{base}` runs inline code"]
        files = [a for a in argv[1:] if not a.startswith("-")]
        if not files:
            return [f"`{base}` reads code from stdin"]
        if not _reviewed_file(files[0], simple.cwd):
            return [f"`{base} {files[0]}` runs a file that is not reviewed repo code"]
        return []
    if base == "awk" and any("system(" in a or "| getline" in a for a in argv[1:]):
        return ["`awk` program calls system()"]
    dev = _dev_tool(argv)
    if dev is not None:
        unreviewed = [p for p in _script_args(dev) if not _reviewed_file(p, simple.cwd)]
        if unreviewed:
            return [f"`{_program(dev)}` would run {unreviewed[0]}, which is not reviewed repo code"]
        return []
    if base in ("uv", "uvx"):
        target = hooklib._uv_target(argv)  # noqa: SLF001
        if target:
            return executors(hooklib.Simple(target, target, [], [], {}, simple.cwd, simple.depth))
        return []
    if base.startswith("python"):
        return _python_exec(argv, simple.cwd)
    if "/" in argv[0] and base not in hooklib.SOURCES and _local_unreviewed(argv[0], simple.cwd):
        return [f"`{argv[0]}` is a local program that is not reviewed repo code"]
    return []


def _local_unreviewed(program: str, cwd: str | None) -> bool:
    """A program run by path from the project (outside .venv) or a temp dir, not reviewed."""
    if cwd is None:
        return True
    full = os.path.realpath(os.path.join(cwd, os.path.expanduser(program)))
    root = _project_root()
    temps = {os.path.realpath(p) for p in ("/tmp", "/private/tmp", "/var/tmp")}
    if os.environ.get("TMPDIR"):
        temps.add(os.path.realpath(os.environ["TMPDIR"]))
    in_project = full.startswith(root + os.sep) and ".venv" not in full[len(root) :].split(os.sep)
    in_temp = any(full.startswith(t + os.sep) for t in temps)
    return (in_project or in_temp) and not _reviewed_file(program, cwd)


def _python_exec(argv: list[str], cwd: str | None) -> list[str]:
    rest = argv[1:]
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "-":
            return ["`python -` reads code from stdin"]
        if arg.startswith("-") and len(arg) > 1 and not arg.startswith("--"):
            for pos, ch in enumerate(arg[1:], start=1):
                if ch in "cmWXQ":
                    value = arg[pos + 1 :] or (rest[i + 1] if i + 1 < len(rest) else "")
                    if ch == "c":
                        return ["`python -c` runs inline code"]
                    if ch == "m":
                        return [] if value in _DEV_TOOLS else [f"`python -m {value}` runs a module"]
                    i += 1 if arg[pos + 1 :] else 2
                    break
            else:
                i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        if _reviewed_file(arg, cwd):
            return []
        return [f"`python {arg}` runs a file that is not reviewed repo code"]
    return ["`python` reads code from stdin"]


def heuristics(analysis: hooklib.Analysis, cli_simples: set[int]) -> list[str]:
    """Reasons to ask about a command that has no recognised write verb in it."""
    command = analysis.command
    reasons: list[str] = []
    execs: list[str] = []
    for simple in analysis.simples:
        execs.extend(executors(simple))

    def only_in_inspectors(pattern: re.Pattern[str]) -> bool:
        """The pattern appears, on a clean parse, only in arguments of inspector programs."""
        if not analysis.clean or execs:
            return False
        for simple in analysis.simples:
            words = simple.tokens + [r for _, r in simple.redirects] + simple.herestrings
            if any(pattern.search(w) for w in words) and not _is_inspector(simple.argv):
                return False
        return True

    if _API_HOST.search(command):
        reasons.append("the command names a Slack/ClickUp API host directly")
    if _TOKEN_NAMES.search(command) or _TOKEN_SHAPES.search(command):
        reasons.append("the command names a Slack/ClickUp token (variable or token-shaped text)")
    if _DOTENV.search(command):
        reasons.append("the command touches a .env file, where the tokens live")
    if _PACKAGES.search(command) and not only_in_inspectors(_PACKAGES):
        reasons.append("the command uses the wall3_slack / wall3_clickup package directly")
    if _CLIENT_DIRS.search(command) and execs:
        reasons.append("the command runs code inside clients/slack or clients/clickup")

    write_word = any(
        re.search(rf"(?<![\w-]){v}(?![\w-])", command) for v in hooklib.ALL_WRITE_VERBS
    )
    for index, simple in enumerate(analysis.simples):
        if index in cli_simples:
            continue
        argv = simple.argv
        base = _program(argv)
        words = simple.tokens + [r for _, r in simple.redirects] + simple.herestrings
        names_cli = any(_CLI_NAME.search(w) for w in words) or any(
            _CLI_NAME.search(v) for v in simple.assigns.values()
        )
        if names_cli and base in _COPIERS:
            reasons.append(f"`{base}` copies, links or aliases the slack/clickup CLI")
        elif names_cli and any(">" in op for op, _ in simple.redirects):
            reasons.append("the slack/clickup CLI is redirected into another file")
        elif names_cli and write_word and (not analysis.clean or execs or not _is_inspector(argv)):
            reasons.append(
                "the slack/clickup CLI is named together with a write verb, in a form the "
                "hook cannot parse as a CLI call"
            )
        if not _is_inspector(argv) and any(a.split("=", 1)[0] in _CLI_SHAPED for a in argv[1:]):
            reasons.append(f"`{base}` takes write-verb options (--text-file/--expect-sha256)")
        if (
            argv
            and (hooklib.is_dynamic(argv[0]) or _GLOB.search(argv[0]))
            and (
                any(a in hooklib.ALL_WRITE_VERBS for a in argv[1:])
                or any(a.split("=", 1)[0] in _CLI_SHAPED | {"--channel"} for a in argv[1:])
            )
        ):
            reasons.append(f"a program computed at run time ({argv[0]}) is given a write verb")

    if execs and (
        reasons
        or _PACKAGES.search(command)
        or _CLIENT_DIRS.search(command)
        or _CLI_NAME.search(command)
    ):
        reasons.extend(execs)
    elif any(e for e in execs if not e.startswith(("`python", "`uv"))):
        # Shells reading scripts, make, perl -e, osascript ...: always ask.
        reasons.extend(e for e in execs if not e.startswith(("`python", "`uv")))
    elif any("not reviewed repo code" in e for e in execs):
        reasons.extend(e for e in execs if "not reviewed repo code" in e)

    seen: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.append(r)
    return seen


# -------------------------------------------------------------------- decide


def describe_mcp(call: hooklib.McpCall, tool_input: Any) -> str:
    try:
        payload = json.dumps(tool_input, indent=1, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(tool_input)
    shown, hidden = render_text(payload)
    lines = [
        f"OUTBOUND {call.source.upper()} WRITE via MCP: {call.server} / {call.action}",
        "  payload:",
        hooklib.truncate(shown, TEXT_LIMIT),
    ]
    if hidden:
        lines.append(f"  WARNING: {hidden} invisible/control character(s) shown as \\uXXXX.")
    lines.append("Approve only if Don has approved this exact payload.")
    return "\n".join(lines)


def decide_bash(command: str, cwd: str) -> tuple[str, str] | None:
    analysis = hooklib.analyze(command, cwd)
    verdict = Verdict()
    cli_simples: set[int] = set()
    index_of = {id(s): i for i, s in enumerate(analysis.simples)}
    for call in analysis.cli_calls():
        if call.simple is not None:
            cli_simples.add(index_of[id(call.simple)])
        kind = call.kind
        if kind == "write":
            decision, reason = check_write(call, analysis, cwd)
            (verdict.denies if decision == "deny" else verdict.asks).append(reason)
        elif kind in ("dynamic", "unknown"):
            verdict.asks.append(
                f"OUTBOUND {call.source.upper()} CLI CALL WITH "
                f"{'A COMPUTED' if kind == 'dynamic' else 'AN UNKNOWN'} VERB: "
                f"`{call.program} {' '.join(call.args[:2])}`\n"
                "  The verb is not a literal read verb, so this may be a write the hook "
                "cannot preview. Approve only if you know what it runs.\n"
                f"  command: {command[:TEXT_LIMIT]}"
            )
    reasons = heuristics(analysis, cli_simples)
    if reasons:
        verdict.asks.append(
            "POSSIBLE SLACK/CLICKUP WRITE (heuristic) -- the hook cannot see what this sends:\n"
            + "\n".join(f"  - {r}" for r in reasons)
            + f"\n  command: {command[:TEXT_LIMIT]}\n"
            "Approve only if you are sure it does not post to Slack or ClickUp. Writes go "
            "through bin/slack send / bin/clickup comment|status "
            "(.claude/rules/messaging-safety.md)."
        )
    return verdict.result()


def decide(event: dict[str, Any]) -> tuple[str, str] | None:
    """(decision, reason) for this tool call, or None to pass silently."""
    tool_name = event.get("tool_name")
    if not isinstance(tool_name, str):
        return None
    command = hooklib.bash_command(event)
    if command is not None:
        return decide_bash(command, hooklib.event_cwd(event))
    mcp = hooklib.classify_mcp(tool_name)
    if mcp is not None and mcp.is_write:
        return "ask", describe_mcp(mcp, event.get("tool_input"))
    return None


def main() -> int:
    event = hooklib.read_event()
    if event is None:
        return 0
    try:
        result = decide(event)
    except Exception as exc:  # fail toward asking, never toward silence
        result = (
            "ask",
            f"outbound_guard could not evaluate this call ({exc!r}); review it by hand.",
        )
    if result:
        respond(*result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
