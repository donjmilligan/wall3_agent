#!/usr/bin/env python3
"""Integration gate: every repo path the harness docs cite actually exists.

Scans ``CLAUDE.md``, ``README.md`` and ``.claude/**/*.md`` for inline
backticked spans that look like repo-relative paths, and fails (exit 1) listing
``file:line`` for each one that exists neither under ``--root`` nor beside the
doc that cites it (so a README may name its siblings bare).

A span is treated as a path when it has no whitespace and none of ``< > { } *
$`` (placeholders/globs), is not absolute, ``~``, ``../`` or a URL, and either
ends in ``/``, has a known file extension (``.md``, ``.py``, ``.json`` ...), is a
dotfile (``.gitignore``), or starts with a directory that exists at the root.
A trailing ``:<line>`` or ``#anchor`` is dropped before the check.
Fenced code blocks are skipped (they hold commands, not citations). Paths git
ignores (e.g. ``.env``, ``.cache/``) are skipped when the root is a git repo:
they are cited precisely because they must never exist in a checkout.

Usage: python scripts/check_harness_refs.py [--root DIR]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
FENCE = re.compile(r"^\s*(```|~~~)")
EXTENSION = re.compile(
    r"\.(md|py|json|jsonl|ya?ml|toml|sh|txt|lock|cfg|ini|csv|html|example)$", re.IGNORECASE
)
LINE_SUFFIX = re.compile(r"(:\d+(-\d+)?)+$")
FORBIDDEN = set("<>{}*$")


@dataclass(frozen=True)
class Ref:
    doc: str
    line: int
    path: str


def doc_files(root: Path) -> list[Path]:
    files = [root / "CLAUDE.md", root / "README.md"]
    files += sorted((root / ".claude").rglob("*.md")) if (root / ".claude").is_dir() else []
    return [f for f in files if f.is_file()]


def as_path(span: str, root: Path) -> str | None:
    span = span.strip()
    if not span or any(c.isspace() for c in span) or FORBIDDEN & set(span):
        return None
    if span.startswith(("/", "~", "../", "-", "@")) or "://" in span:
        return None
    span = LINE_SUFFIX.sub("", span.split("#", 1)[0])
    if not span or span in (".", "./"):
        return None
    span = span.removeprefix("./")
    if EXTENSION.fullmatch(span):  # a bare extension such as `.md` is not a path
        return None
    first = span.split("/", 1)[0]
    if span.endswith("/") or EXTENSION.search(span) or (span.startswith(".") and len(span) > 1):
        return span
    if "/" in span and (root / first).is_dir():
        return span
    return None


def find_refs(root: Path) -> tuple[list[Path], list[Ref]]:
    docs = doc_files(root)
    refs: list[Ref] = []
    for doc in docs:
        in_fence = False
        rel_doc = doc.relative_to(root).as_posix()
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            if FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in INLINE_CODE.finditer(line):
                path = as_path(match.group(1), root)
                if path:
                    refs.append(Ref(rel_doc, lineno, path))
    return docs, refs


def exists(root: Path, ref: Ref) -> bool:
    """Resolve against the root, or against the citing doc's own directory."""
    return (root / ref.path).exists() or ((root / ref.doc).parent / ref.path).exists()


def git_ignored(root: Path, paths: list[str]) -> set[str]:
    if not paths or not (root / ".git").exists():
        return set()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--no-index", "--stdin"],
            input="\n".join(paths),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(proc.stdout.splitlines())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()

    docs, refs = find_refs(root)
    if not docs:
        print(f"FAIL no harness docs found under {root} (CLAUDE.md, README.md, .claude/)")
        return 1
    missing = [r for r in refs if not exists(root, r)]
    ignored = git_ignored(root, sorted({r.path for r in missing}))
    missing = [r for r in missing if r.path not in ignored]

    for ref in missing:
        print(f"MISSING {ref.doc}:{ref.line}: {ref.path}")
    distinct = len({r.path for r in refs})
    print(
        f"harness refs: {len(refs)} reference(s) to {distinct} path(s) in {len(docs)} doc(s); "
        f"{len(missing)} missing"
    )
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
