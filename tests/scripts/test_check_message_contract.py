"""scripts/check_message_contract.py against a fake repo with fake CLIs (no B/C needed)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_message_contract.py"

# The fake CLI prints <fixtures>/out.jsonl and exits with <fixtures>/exit (default 0).
FAKE_CLI = f"""#!{sys.executable}
import pathlib, sys
assert sys.argv[1] == "normalize" and sys.argv[3] == "--json", sys.argv
fixtures = pathlib.Path(sys.argv[2])
out = fixtures / "out.jsonl"
if out.exists():
    sys.stdout.write(out.read_text())
code = fixtures / "exit"
if code.exists():
    sys.stderr.write("simulated failure\\n")
    sys.exit(int(code.read_text()))
"""


def slack_message(**over: Any) -> dict[str, Any]:
    msg = {
        "source": "slack",
        "id": "C0TEST:1730729002.000100",
        "thread_id": None,
        "container": {"kind": "channel", "id": "C0TEST", "name": "general"},
        "author": {"id": "U0TEST", "name": "Alex Example"},
        "ts": "2026-11-04T14:03:22Z",
        "text": "@Don can you review?",
        "permalink": "https://example.slack.com/archives/C0TEST/p1730729002000100",
        "is_mention": True,
        "is_dm": False,
        "awaiting_reply": True,
    }
    msg.update(over)
    return msg


def clickup_message(**over: Any) -> dict[str, Any]:
    msg = {
        "source": "clickup",
        "id": "90000001",
        "thread_id": "86testtask",
        "container": {"kind": "task", "id": "86testtask", "name": "Synthetic task"},
        "author": {"id": "1001", "name": "Sam Example"},
        "ts": "2026-11-04T15:00:00Z",
        "text": "Status?",
        "permalink": "https://app.clickup.com/t/86testtask",
        "is_mention": False,
        "is_dm": False,
        "awaiting_reply": False,
    }
    msg.update(over)
    return msg


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "contracts").mkdir(parents=True)
    shutil.copy(REPO / "docs/contracts/message.schema.json", tmp_path / "docs/contracts/")
    (tmp_path / "bin").mkdir()
    for source in ("slack", "clickup"):
        cli = tmp_path / "bin" / source
        cli.write_text(FAKE_CLI)
        cli.chmod(0o755)
        (tmp_path / "clients" / source / "tests/fixtures/raw/inbox").mkdir(parents=True)
    return tmp_path


def emit(root: Path, source: str, lines: list[Any]) -> None:
    out = root / "clients" / source / "tests/fixtures/raw/inbox/out.jsonl"
    out.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines))


def run(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_output_from_both_sources_passes(fake_root):
    emit(fake_root, "slack", [slack_message(), slack_message(thread_id="C0TEST:1730729002.000100")])
    emit(
        fake_root,
        "clickup",
        [
            clickup_message(),
            "",
            clickup_message(container={"kind": "chat", "id": "chan1", "name": "Synthetic chat"}),
        ],
    )
    result = run(fake_root)
    assert result.returncode == 0, result.stderr
    assert "slack: 2 message(s) checked" in result.stdout
    assert "clickup: 2 message(s) checked" in result.stdout
    assert "message contract: OK" in result.stdout


def test_zero_lines_is_a_failure(fake_root):
    emit(fake_root, "slack", [slack_message()])
    emit(fake_root, "clickup", [])
    result = run(fake_root)
    assert result.returncode == 1
    assert "clickup: `bin/clickup normalize ... --json` produced zero messages" in result.stderr
    assert "slack: 1 message(s) checked" in result.stdout


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        (slack_message(extra="x"), "Additional properties"),
        (slack_message(ts="2026-11-04T14:03:22+00:00"), "line 1: ts"),
        ({k: v for k, v in slack_message().items() if k != "awaiting_reply"}, "awaiting_reply"),
        (slack_message(id="not-a-slack-ref"), "line 1: id"),
        (slack_message(container={"kind": "task", "id": "x", "name": "y"}), "container/kind"),
        (slack_message(is_dm="no"), "line 1: is_dm"),
    ],
)
def test_schema_violations_fail_with_line_and_field(fake_root, bad, needle):
    emit(fake_root, "slack", [bad])
    emit(fake_root, "clickup", [clickup_message()])
    result = run(fake_root)
    assert result.returncode == 1
    assert needle in result.stderr
    assert "FAIL slack line 1" in result.stderr
    assert "FAIL clickup" not in result.stderr


def test_source_mismatch_fails(fake_root):
    emit(fake_root, "slack", [slack_message()])
    emit(fake_root, "clickup", [slack_message()])
    result = run(fake_root)
    assert result.returncode == 1
    assert "clickup line 1: source is 'slack', expected 'clickup'" in result.stderr


def test_non_json_line_fails(fake_root):
    emit(fake_root, "slack", ["{not json"])
    emit(fake_root, "clickup", [clickup_message()])
    result = run(fake_root)
    assert result.returncode == 1
    assert "slack line 1: not JSON" in result.stderr


def test_command_failure_fails(fake_root):
    emit(fake_root, "slack", [slack_message()])
    (fake_root / "clients/slack/tests/fixtures/raw/inbox/exit").write_text("1")
    emit(fake_root, "clickup", [clickup_message()])
    result = run(fake_root)
    assert result.returncode == 1
    assert "exited 1: simulated failure" in result.stderr


def test_missing_cli_fails(fake_root):
    (fake_root / "bin" / "clickup").unlink()
    emit(fake_root, "slack", [slack_message()])
    result = run(fake_root)
    assert result.returncode == 1
    assert "clickup: `bin/clickup normalize ... --json` could not run" in result.stderr


def test_source_filter_runs_only_that_source(fake_root):
    emit(fake_root, "slack", [slack_message()])
    (fake_root / "bin" / "clickup").unlink()
    result = run(fake_root, "--source", "slack")
    assert result.returncode == 0, result.stderr
    assert "slack: 1 message(s) checked" in result.stdout
