"""ci.yml: no checkout leaves the job's GITHUB_TOKEN in .git/config for later steps."""

from __future__ import annotations

import re
from pathlib import Path

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def checkout_steps(text: str) -> list[str]:
    """Each `uses: actions/checkout` step, with the indented lines that belong to it."""
    lines = text.splitlines()
    steps = []
    for i, line in enumerate(lines):
        if re.match(r"\s*- uses: actions/checkout@", line):
            indent = len(line) - len(line.lstrip())
            block = [line]
            for nxt in lines[i + 1 :]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) < indent + 2:
                    break
                block.append(nxt)
            steps.append("\n".join(block))
    return steps


def test_every_checkout_disables_persisted_credentials():
    steps = checkout_steps(CI.read_text())
    assert len(steps) == 3  # root, packages, integration
    for step in steps:
        assert re.search(r"^\s+persist-credentials: false$", step, re.M), step
