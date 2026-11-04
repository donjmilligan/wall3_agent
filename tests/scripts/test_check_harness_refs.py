"""scripts/check_harness_refs.py against synthetic doc trees (presence AND absence)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_harness_refs.py"


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def tree(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_all_present_passes_and_counts_refs(tmp_path):
    root = tree(
        tmp_path,
        {
            "CLAUDE.md": "See `docs/a.md` and `bin/tool`.\nAlso `.claude/rules/r.md`.\n",
            "docs/a.md": "",
            "bin/tool": "",
            ".claude/rules/r.md": "Back to `CLAUDE.md`.\n",
        },
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "4 reference(s) to 4 path(s) in 2 doc(s); 0 missing" in result.stdout


def test_missing_paths_fail_with_file_and_line(tmp_path):
    root = tree(
        tmp_path,
        {
            "CLAUDE.md": "ok `docs/a.md`\n\nbad `docs/missing.md`\n",
            "docs/a.md": "",
            ".claude/agents/README.md": "x\nlists `.claude/agents/ghost.md` here\n",
            "README.md": "dir `tools/messaging/`\n",
        },
    )
    result = run(root)
    assert result.returncode == 1
    assert "MISSING CLAUDE.md:3: docs/missing.md" in result.stdout
    assert "MISSING .claude/agents/README.md:2: .claude/agents/ghost.md" in result.stdout
    assert "MISSING README.md:1: tools/messaging/" in result.stdout
    assert "docs/a.md" not in result.stdout.split("harness refs:")[0]


def test_non_paths_are_ignored(tmp_path):
    spans = [
        "/abs/path.md",
        "~/notes.md",
        "../sibling/x.md",
        "https://example.com/x.md",
        "feature/<slug>",
        "clients/**",
        "$HOME/x.md",
        "{a,b}.md",
        "origin/main",
        "bin/slack inbox --json",
        "tool_input.command",
        "--text-file",
        "permissionDecision",
        ".md",
        ".py",
    ]
    body = "\n".join(f"- `{s}`" for s in spans) + "\n- `docs/real.md`\n"
    root = tree(tmp_path, {"CLAUDE.md": body, "docs/real.md": ""})
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "1 reference(s)" in result.stdout


def test_line_suffix_and_anchor_are_stripped(tmp_path):
    root = tree(
        tmp_path,
        {"CLAUDE.md": "`docs/a.md:12` `docs/a.md#intro` `docs/b.md:3-9`\n", "docs/a.md": ""},
    )
    result = run(root)
    assert result.returncode == 1
    assert "MISSING CLAUDE.md:1: docs/b.md" in result.stdout
    assert "3 reference(s) to 2 path(s)" in result.stdout


def test_fenced_code_blocks_are_skipped(tmp_path):
    body = "```bash\ncat `docs/inside-fence.md`\n```\nafter `docs/outside.md`\n"
    root = tree(tmp_path, {"CLAUDE.md": body})
    result = run(root)
    assert result.returncode == 1
    assert "docs/outside.md" in result.stdout
    assert "inside-fence" not in result.stdout


def test_gitignored_paths_are_skipped_only_in_a_git_repo(tmp_path):
    files = {"CLAUDE.md": "never commit `.env` or `.cache/`\n", ".gitignore": ".env\n.cache/\n"}
    root = tree(tmp_path, files)
    without_git = run(root)
    assert without_git.returncode == 1
    assert "MISSING CLAUDE.md:1: .env" in without_git.stdout
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    with_git = run(root)
    assert with_git.returncode == 0, with_git.stdout
    assert "2 reference(s)" in with_git.stdout


def test_no_docs_is_a_failure(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 1
    assert "no harness docs found" in result.stdout


def test_bare_sibling_names_resolve_beside_the_citing_doc(tmp_path):
    root = tree(
        tmp_path,
        {
            ".claude/hooks/README.md": "`guard.py` and `ghost.py`\n",
            ".claude/hooks/guard.py": "",
            "CLAUDE.md": "`guard.py` from the root\n",
        },
    )
    result = run(root)
    assert result.returncode == 1
    assert "MISSING .claude/hooks/README.md:1: ghost.py" in result.stdout
    assert "MISSING CLAUDE.md:1: guard.py" in result.stdout
    assert ".claude/hooks/README.md:1: guard.py" not in result.stdout
