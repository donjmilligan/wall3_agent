"""Writes raw API responses under a fresh `<root>/<UTC timestamp>/` directory."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: API ids become path components; anything else is refused rather than
#: sanitised, because normalize/ reads ids back from directory names and a
#: rewritten id would silently point at the wrong task.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.:-]+$")


def safe_component(value: str) -> str:
    # fullmatch: with match(), `$` also matches before a trailing newline ("x\n").
    if not _SAFE_COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"refusing to use {value!r} as a path component")
    return value


class RawWriter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def create(cls, root: Path, *, now: datetime | None = None) -> RawWriter:
        stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
        candidate = root / stamp
        suffix = 1
        while candidate.exists():
            candidate = root / f"{stamp}-{suffix}"
            suffix += 1
        candidate.mkdir(parents=True)
        return cls(candidate)

    def write(self, relative: str, data: Any) -> Path:
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return path
