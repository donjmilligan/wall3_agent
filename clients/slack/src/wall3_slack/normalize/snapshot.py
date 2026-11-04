"""Load a raw directory written by ``extract/`` into plain in-memory structures.

Layout (every file except ``manifest.json`` is a Slack response, verbatim)::

    manifest.json                               run record: verb, window, truncation
    whoami.json                                 auth.test
    conversations.list/<label>-page-NNN.json
    conversations.info/<channel>.json
    conversations.history/<channel>/page-NNN.json
    conversations.replies/<channel>-<ts>/page-NNN.json
    search.messages/<label>/page-NNN.json
    users.info/<user>.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fields import user_display_name

#: Provenance ranks: a message seen in history/replies carries thread_ts and
#: subtype; a search match does not, so it loses a tie.
ORIGIN_RANK = {"history": 0, "replies": 0, "search": 1}


class NormalizeError(RuntimeError):
    """The raw directory is missing something normalize cannot do without."""


@dataclass(frozen=True)
class RawRecord:
    channel: str
    message: dict[str, Any]
    origin: str
    search_channel: dict[str, Any] | None = None


@dataclass
class Snapshot:
    me: str
    base_url: str
    manifest: dict[str, Any]
    users: dict[str, str] = field(default_factory=dict)
    conversations: dict[str, dict[str, Any]] = field(default_factory=dict)
    records: list[RawRecord] = field(default_factory=list)


def _read(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_snapshot(raw_dir: Path) -> Snapshot:
    if not raw_dir.is_dir():
        raise NormalizeError(f"raw directory not found: {raw_dir}")

    whoami_path = raw_dir / "whoami.json"
    if not whoami_path.is_file():
        raise NormalizeError(
            f"{whoami_path} is missing; normalize needs auth.test to know who 'me' is "
            "and to build permalinks."
        )
    whoami = _read(whoami_path)
    me = whoami.get("user_id")
    base_url = whoami.get("url")
    if not me or not base_url:
        raise NormalizeError(f"{whoami_path} lacks user_id/url (is it an auth.test response?)")

    manifest_path = raw_dir / "manifest.json"
    manifest = _read(manifest_path) if manifest_path.is_file() else {}
    discovery_only = set(manifest.get("discovery_only") or [])

    snap = Snapshot(me=str(me), base_url=str(base_url), manifest=manifest)

    for path in sorted((raw_dir / "users.info").glob("*.json")):
        user = _read(path).get("user")
        if isinstance(user, dict) and user.get("id"):
            snap.users[str(user["id"])] = user_display_name(user)

    for path in sorted((raw_dir / "conversations.list").glob("*.json")):
        for conv in _read(path).get("channels") or []:
            if isinstance(conv, dict) and conv.get("id"):
                snap.conversations[str(conv["id"])] = conv

    for path in sorted((raw_dir / "conversations.info").glob("*.json")):
        conv = _read(path).get("channel")
        if isinstance(conv, dict) and conv.get("id"):
            snap.conversations[str(conv["id"])] = conv

    for page_path in sorted((raw_dir / "conversations.history").glob("*/*.json")):
        channel = page_path.parent.name
        for msg in _read(page_path).get("messages") or []:
            if isinstance(msg, dict) and msg.get("ts"):
                snap.records.append(RawRecord(channel, msg, "history"))

    for page_path in sorted((raw_dir / "conversations.replies").glob("*/*.json")):
        channel = page_path.parent.name.partition("-")[0]
        for msg in _read(page_path).get("messages") or []:
            if isinstance(msg, dict) and msg.get("ts"):
                snap.records.append(RawRecord(channel, msg, "replies"))

    for page_path in sorted((raw_dir / "search.messages").glob("*/*.json")):
        label = page_path.parent.name
        if f"search.messages/{label}" in discovery_only:
            continue
        matches = (_read(page_path).get("messages") or {}).get("matches") or []
        for match in matches:
            if not isinstance(match, dict) or not match.get("ts"):
                continue
            channel_obj = match.get("channel") if isinstance(match.get("channel"), dict) else {}
            channel_id = str((channel_obj or {}).get("id") or "")
            if not channel_id:
                continue
            snap.records.append(RawRecord(channel_id, match, "search", channel_obj))

    return snap
