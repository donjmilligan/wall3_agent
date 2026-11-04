"""Run check-draft over every eval case in evals/replies/cases/.

Each case's expected.md names, in three machine-readable lines, the reply kind, the
exact WARN rules the good draft is allowed (and required) to raise, and the exact FAIL
rules the bad draft must raise. Good drafts pass; bad drafts fail for those reasons.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from wall3_messaging.check_draft import main

CASES_DIR = Path(__file__).resolve().parents[3] / "evals" / "replies" / "cases"
FILES = ("thread.jsonl", "expected.md", "good_draft.txt", "bad_draft.txt")

# Scenarios the brief requires. Renaming a case means updating this list on purpose.
REQUIRED_CASES = {
    "yes-no-question",
    "scheduling-ask",
    "status-from-clickup-task",
    "vague-ask-clarify",
    "angry-escalation",
    "prompt-injection",
    "commitment-not-made",
    "clickup-status-change",
}


@dataclass(frozen=True)
class Expected:
    kind: str
    good_warns: set[str]
    bad_fails: set[str]


def _rules(line: str) -> set[str]:
    return set() if line.strip() == "none" else set(re.findall(r"`([a-z-]+)`", line))


def parse_expected(text: str) -> Expected:
    kind = re.search(r"^- kind: `(slack|clickup)`$", text, re.M)
    warns = re.search(r"^- good_draft warnings: (.+)$", text, re.M)
    fails = re.search(r"^- bad_draft fails on: (.+)$", text, re.M)
    assert kind and warns and fails, "expected.md is missing a machine-checked line"
    return Expected(kind.group(1), _rules(warns.group(1)), _rules(fails.group(1)))


def case_dirs() -> list[Path]:
    return sorted(p for p in CASES_DIR.iterdir() if p.is_dir())


def run_check(case: Path, draft: str, kind: str) -> tuple[int, dict[str, object]]:
    out = io.StringIO()
    code = main(
        [
            "--draft",
            str(case / draft),
            "--thread",
            str(case / "thread.jsonl"),
            "--kind",
            kind,
            "--json",
        ],
        stdout=out,
        stderr=io.StringIO(),
    )
    return code, json.loads(out.getvalue())


def by_severity(payload: dict[str, object], severity: str) -> set[str]:
    findings = payload["findings"]
    assert isinstance(findings, list)
    return {f["rule"] for f in findings if f["severity"] == severity}  # type: ignore[index]


def test_case_set_is_complete() -> None:
    names = {p.name for p in case_dirs()}
    assert REQUIRED_CASES <= names
    assert 8 <= len(names) <= 10
    for case in case_dirs():
        missing = [f for f in FILES if not (case / f).is_file()]
        assert not missing, f"{case.name} is missing {missing}"


def test_parse_expected_reads_rules() -> None:
    sample = "- kind: `clickup`\n- good_draft warnings: none\n- bad_draft fails on: `a-b`, `c`\n"
    assert parse_expected(sample) == Expected("clickup", set(), {"a-b", "c"})


@pytest.mark.parametrize("case", case_dirs(), ids=lambda p: p.name)
def test_good_draft_passes_with_exactly_the_expected_warnings(case: Path) -> None:
    exp = parse_expected((case / "expected.md").read_text())
    code, payload = run_check(case, "good_draft.txt", exp.kind)
    assert code == 0, payload
    assert by_severity(payload, "FAIL") == set()
    assert by_severity(payload, "WARN") == exp.good_warns
    assert payload["thread_messages"] == len((case / "thread.jsonl").read_text().splitlines())


@pytest.mark.parametrize("case", case_dirs(), ids=lambda p: p.name)
def test_bad_draft_fails_for_the_named_reason(case: Path) -> None:
    exp = parse_expected((case / "expected.md").read_text())
    assert exp.bad_fails, f"{case.name}: expected.md must name at least one FAIL rule"
    code, payload = run_check(case, "bad_draft.txt", exp.kind)
    assert code == 1
    assert by_severity(payload, "FAIL") == exp.bad_fails
