#!/usr/bin/env python3
"""Integration gate: both CLIs emit Messages that satisfy the shared contract.

For each source (slack, clickup) this runs, offline::

    bin/<source> normalize <root>/clients/<source>/tests/fixtures/raw/inbox --json

parses the JSONL it prints, and validates every line against
``docs/contracts/message.schema.json`` (JSON Schema 2020-12). It also checks
each line's ``source`` matches the CLI that produced it.

Fails (exit 1) if a command errors, a line is not JSON or violates the schema,
or a source produces ZERO lines — the positive control: an empty run proves
nothing about the contract.

Usage: python scripts/check_message_contract.py [--root DIR] [--source slack ...]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SOURCES = ("slack", "clickup")
TIMEOUT = 300


def load_validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def check_source(root: Path, source: str, validator: Draft202012Validator) -> list[str]:
    fixtures = root / "clients" / source / "tests" / "fixtures" / "raw" / "inbox"
    cmd = [str(root / "bin" / source), "normalize", str(fixtures), "--json"]
    label = f"{source}: `bin/{source} normalize ... --json`"
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd=root, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"{label} could not run: {exc}"]
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-5:]
        return [f"{label} exited {proc.returncode}: " + " | ".join(tail)]

    errors: list[str] = []
    count = 0
    for lineno, line in enumerate(proc.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        count += 1
        try:
            message: Any = json.loads(line)
        except ValueError as exc:
            errors.append(f"{source} line {lineno}: not JSON ({exc})")
            continue
        for err in sorted(validator.iter_errors(message), key=lambda e: list(e.path)):
            where = "/".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"{source} line {lineno}: {where}: {err.message}")
        if isinstance(message, dict) and message.get("source") != source:
            errors.append(
                f"{source} line {lineno}: source is {message.get('source')!r}, expected {source!r}"
            )
    if count == 0:
        errors.append(f"{label} produced zero messages from {fixtures} (positive control)")
    else:
        print(f"{source}: {count} message(s) checked")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source", action="append", choices=SOURCES, dest="sources")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    validator = load_validator(root / "docs" / "contracts" / "message.schema.json")

    errors: list[str] = []
    for source in args.sources or SOURCES:
        errors += check_source(root, source, validator)
    for error in errors:
        print(f"FAIL {error}", file=sys.stderr)
    if errors:
        print(f"message contract: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    print("message contract: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
