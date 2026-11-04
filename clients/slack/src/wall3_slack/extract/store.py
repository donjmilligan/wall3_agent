"""Writing raw responses to disk, verbatim. No transformation happens here."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

#: API ids become path components; anything else is refused rather than
#: sanitised (same rule as wall3_clickup's raw_writer.safe_component): a channel,
#: ts or user id like ``../../x`` must never steer a write outside the run dir.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.:-]+$")


class UnsafeComponentError(ValueError):
    """An id that is not safe to use as a path component."""


def safe_component(value: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise UnsafeComponentError(f"refusing to use {value!r} as a path component")
    return value


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
