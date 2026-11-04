"""Builders for synthetic Message fixtures. Every name, ID and URL here is fictional."""

from __future__ import annotations

import json
from typing import Any


def msg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source": "slack",
        "id": "CFAKE01:1000.0001",
        "thread_id": None,
        "container": {"kind": "channel", "id": "CFAKE01", "name": "acme-dev"},
        "author": {"id": "UFAKEALICE", "name": "Alice Example"},
        "ts": "2026-11-04T12:00:00Z",
        "text": "hello",
        "permalink": "https://slack.acme.example/archives/CFAKE01/p10000001",
        "is_mention": False,
        "is_dm": False,
        "awaiting_reply": False,
    }
    base.update(overrides)
    return base


def jsonl(*items: dict[str, Any] | str) -> str:
    return "".join((i if isinstance(i, str) else json.dumps(i)) + "\n" for i in items)
