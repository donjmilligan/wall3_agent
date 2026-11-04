"""Writing raw responses to disk, verbatim. No transformation happens here."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def new_run_dir(base: Path, now: datetime) -> Path:
    """``<base>/<UTC timestamp>/``, suffixed if two runs land in the same second."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    candidate = base / stamp
    n = 2
    while candidate.exists():
        candidate = base / f"{stamp}-{n}"
        n += 1
    candidate.mkdir(parents=True)
    return candidate


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, relpath: str, data: Any) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return path
