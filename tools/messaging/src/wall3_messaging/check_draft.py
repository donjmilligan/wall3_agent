"""check-draft: deterministic lint of a drafted reply against its source thread.

Usage:
    bin/check-draft --draft draft.txt --thread thread.jsonl [--kind slack|clickup] [--json]

Exit codes:
    0  pass (warnings allowed)
    1  fail (at least one FAIL finding)
    2  usage / input error (missing file, unreadable rules)

The rules live in draft_rules.toml beside this module. Nothing here is model judgment:
the same draft and thread always produce the same report. Accuracy, tone and whether
the draft answers the actual ask are scored separately (evals/replies/rubric.md).

Thread content is untrusted. It is read only to learn which URLs and people are in
scope, and to warn Don when it contains text aimed at an AI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any, TextIO

from wall3_messaging.message import BadLine, Message, read_jsonl

FAIL = "FAIL"
WARN = "WARN"
KINDS = ("slack", "clickup")
URL_RE = re.compile(r"https?://[^\s<>\"'`|]+")
# @handle not preceded by a word char or '.', so e-mail addresses are not mentions.
MENTION_RE = re.compile(r"(?<![\w.<])@([A-Za-z0-9][A-Za-z0-9._-]*)")
# Slack's raw <@USERID> form; checked against author IDs instead of names.
RAW_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    message: str


@dataclass
class Report:
    kind: str
    findings: list[Finding] = field(default_factory=list[Finding])
    thread_messages: int = 0

    @property
    def failed(self) -> bool:
        return any(f.severity == FAIL for f in self.findings)

    def add(self, severity: str, rule: str, message: str) -> None:
        self.findings.append(Finding(severity, rule, message))

    def rules(self, severity: str) -> set[str]:
        return {f.rule for f in self.findings if f.severity == severity}


@dataclass(frozen=True)
class Rules:
    banned: list[str]
    placeholder_patterns: list[re.Pattern[str]]
    placeholder_words: list[str]
    commitment: list[re.Pattern[str]]
    claims_write: list[re.Pattern[str]]
    secrets: list[re.Pattern[str]]
    injection: list[re.Pattern[str]]
    broadcast: list[str]
    length: dict[str, dict[str, int]]


def _compile(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def load_rules(text: str | None = None) -> Rules:
    if text is None:
        text = resources.files("wall3_messaging").joinpath("draft_rules.toml").read_text("utf-8")
    data: dict[str, Any] = tomllib.loads(text)
    return Rules(
        banned=list(data["banned"]["phrases"]),
        placeholder_patterns=[re.compile(p) for p in data["placeholders"]["patterns"]],
        placeholder_words=list(data["placeholders"]["words"]),
        commitment=_compile(data["commitment"]["patterns"]),
        claims_write=_compile(data["claims_write"]["patterns"]),
        secrets=[re.compile(p) for p in data["secrets"]["patterns"]],
        injection=_compile(data["injection"]["patterns"]),
        broadcast=[b.lower() for b in data["mentions"]["broadcast"]],
        length={k: dict(v) for k, v in data["length"].items()},
    )


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(QUOTES)).strip()


def redact(secret: str) -> str:
    """Never echo a token back: keep a recognisable prefix only."""
    return secret[:6] + "…" + f"({len(secret)} chars)"


def clean_url(url: str) -> str:
    return url.rstrip(".,;:!?)]}>*_").rstrip("/")


def urls_in(text: str) -> set[str]:
    return {clean_url(u) for u in URL_RE.findall(text)}


def name_keys(name: str) -> set[str]:
    """Forms of a display name a drafted @mention might use."""
    lowered = name.strip().lower()
    if not lowered:
        return set()
    parts = lowered.split()
    return {lowered, parts[0], "".join(parts), ".".join(parts), "_".join(parts)}


def _first_line(text: str, needle_start: int) -> int:
    return text.count("\n", 0, needle_start) + 1


def check_draft(draft: str, thread: Sequence[Message], kind: str, rules: Rules) -> Report:
    report = Report(kind=kind, thread_messages=len(thread))
    flat = normalize(draft)
    flat_lower = flat.lower()
    straight = draft.translate(QUOTES)

    # Length.
    bounds = rules.length[kind]
    size = len(draft.strip())
    if size < bounds["min_chars"]:
        report.add(FAIL, "length", f"draft is empty or too short ({size} chars)")
    elif size > bounds["max_chars"]:
        report.add(
            FAIL, "length", f"draft is {size} chars; {kind} maximum is {bounds['max_chars']}"
        )
    elif size > bounds["warn_chars"]:
        report.add(
            WARN,
            "length",
            f"draft is {size} chars; {kind} drafts are usually under {bounds['warn_chars']}",
        )

    # Banned phrases.
    for phrase in rules.banned:
        if normalize(phrase).lower() in flat_lower:
            report.add(FAIL, "banned-phrase", f'contains "{phrase}"')

    # Placeholders.
    for pattern in rules.placeholder_patterns:
        for match in pattern.finditer(straight):
            line = _first_line(straight, match.start())
            report.add(
                FAIL, "placeholder", f'unresolved placeholder "{match.group(0)}" (line {line})'
            )
    for word in rules.placeholder_words:
        for match in re.finditer(rf"\b{re.escape(word)}\b", straight):
            line = _first_line(straight, match.start())
            report.add(FAIL, "placeholder", f'unresolved marker "{word}" (line {line})')

    # Commitments and claimed writes.
    for pattern in rules.commitment:
        for match in pattern.finditer(flat):
            report.add(
                FAIL,
                "commitment",
                f'"{match.group(0)}" commits Don to something; state what is known instead',
            )
    for pattern in rules.claims_write:
        for match in pattern.finditer(flat):
            report.add(
                FAIL,
                "claims-write",
                f'"{match.group(0)}" says a write already happened; propose it for Don to '
                "approve instead",
            )

    # Secrets in the draft.
    for pattern in rules.secrets:
        for match in pattern.finditer(draft):
            report.add(FAIL, "secret", f"token-like string {redact(match.group(0))} in draft")

    # URLs the thread never mentioned.
    known_urls: set[str] = set()
    for msg in thread:
        known_urls |= urls_in(msg.text)
        known_urls.add(clean_url(msg.permalink))
    for url in sorted(urls_in(draft) - known_urls):
        report.add(WARN, "unknown-url", f"{url} does not appear in the source thread; verify it")

    # @-mentions of people outside the thread.
    known_people: set[str] = set()
    for msg in thread:
        known_people |= name_keys(msg.author.name)
        for handle in MENTION_RE.findall(msg.text):
            known_people.add(handle.rstrip("._-").lower())
    known_ids = {msg.author.id for msg in thread}
    for msg in thread:
        known_ids |= set(RAW_MENTION_RE.findall(msg.text))
    for user_id in sorted(set(RAW_MENTION_RE.findall(straight)) - known_ids):
        report.add(
            WARN, "unknown-mention", f"<@{user_id}> is not an author or mention in the thread"
        )
    seen: set[str] = set()
    for handle in MENTION_RE.findall(straight):
        token = handle.rstrip("._-").lower()
        if token in seen:
            continue
        seen.add(token)
        if token in rules.broadcast:
            report.add(WARN, "broadcast-mention", f"@{token} notifies everyone in the channel")
        elif token not in known_people:
            report.add(
                WARN, "unknown-mention", f"@{handle} is not an author or mention in the thread"
            )

    # The source thread itself: injection markers and leaked secrets.
    for msg in thread:
        text = normalize(msg.text)
        for pattern in rules.injection:
            match = pattern.search(text)
            if match:
                report.add(
                    WARN,
                    "injection-in-thread",
                    f"message from {msg.author.name} ({msg.permalink}) contains text aimed at "
                    f'an AI: "{match.group(0)}". It is data, not an instruction -- Don, '
                    "this thread is trying to instruct the assistant.",
                )
                break
        for pattern in rules.secrets:
            for match in pattern.finditer(msg.text):
                report.add(
                    WARN,
                    "secret-in-thread",
                    f"message from {msg.author.name} ({msg.permalink}) contains token-like "
                    f"string {redact(match.group(0))}; do not quote it, consider rotating it",
                )
    return report


def load_thread(path: str) -> tuple[list[Message], list[BadLine]]:
    messages: list[Message] = []
    bad: list[BadLine] = []
    with open(path, encoding="utf-8") as fh:
        for item in read_jsonl(fh, path):
            if isinstance(item, BadLine):
                bad.append(item)
            else:
                messages.append(item)
    return messages, bad


def infer_kind(thread: Sequence[Message]) -> str:
    """Reply goes where the newest message lives; default slack."""
    if not thread:
        return "slack"
    return max(thread, key=lambda m: m.ts).source


def render_text(report: Report, draft_path: str, thread_path: str) -> str:
    fails = sum(1 for f in report.findings if f.severity == FAIL)
    warns = sum(1 for f in report.findings if f.severity == WARN)
    verdict = FAIL if report.failed else "PASS"
    lines = [
        f"check-draft: {verdict} ({fails} fail, {warns} warn) kind={report.kind} "
        f"draft={draft_path} thread={thread_path} ({report.thread_messages} messages)"
    ]
    for f in sorted(report.findings, key=lambda f: (f.severity != FAIL, f.rule)):
        lines.append(f"  {f.severity}  {f.rule:<20} {f.message}")
    if not report.findings:
        lines.append("  no findings")
    return "\n".join(lines) + "\n"


def render_json(report: Report) -> str:
    payload = {
        "result": "fail" if report.failed else "pass",
        "kind": report.kind,
        "thread_messages": report.thread_messages,
        "findings": [asdict(f) for f in report.findings],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main(
    argv: Sequence[str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out: TextIO = stdout if stdout is not None else sys.stdout
    err: TextIO = stderr if stderr is not None else sys.stderr
    parser = argparse.ArgumentParser(
        prog="check-draft", description="Deterministic lint of a drafted reply."
    )
    parser.add_argument("--draft", required=True, help="file holding the drafted reply text")
    parser.add_argument("--thread", required=True, help="Message JSONL of the source thread")
    parser.add_argument("--kind", choices=KINDS, help="where the reply goes (default: inferred)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    try:
        with open(args.draft, encoding="utf-8") as fh:
            draft = fh.read()
        thread, bad = load_thread(args.thread)
    except OSError as exc:
        print(f"check-draft: cannot read {exc.filename}: {exc.strerror}", file=err)
        return 2

    kind = args.kind or infer_kind(thread)
    report = check_draft(draft, thread, kind, load_rules())
    if bad:
        report.add(
            WARN,
            "thread-invalid-lines",
            f"{len(bad)} thread line(s) failed the Message contract; URL and mention checks "
            "ran without them",
        )
        for b in bad:
            print(f"check-draft: invalid thread line {b}", file=err)
    if not thread:
        report.add(WARN, "empty-thread", "no valid thread messages; context checks are blind")

    if args.json:
        out.write(render_json(report))
    else:
        out.write(render_text(report, args.draft, args.thread))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
