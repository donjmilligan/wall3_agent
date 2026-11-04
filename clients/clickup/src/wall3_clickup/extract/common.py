"""Pieces every extract shares: identity, workspace members, the manifest."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..client import ClickUpClient
from .raw_writer import RawWriter


def write_identity(client: ClickUpClient, writer: RawWriter) -> str:
    """Write `whoami.json` (GET /v2/user) and `teams.json` (GET /v2/team).

    Returns my user id as a string -- needed to decide which follow-up reads
    are worth making (e.g. tagged-user lookups on messages I did not write).
    """
    user = client.get_user()
    writer.write("whoami.json", user)
    writer.write("teams.json", client.get_teams())
    return str((user.get("user") or {}).get("id", ""))


def write_manifest(
    writer: RawWriter,
    *,
    kind: str,
    team_id: str,
    since_ms: int | None,
    notes: list[str],
    extra: dict[str, Any] | None = None,
) -> None:
    manifest: dict[str, Any] = {
        "kind": kind,
        "team_id": team_id,
        "since_ms": since_ms,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": notes,
    }
    if extra:
        manifest.update(extra)
    writer.write("manifest.json", manifest)


def page_name(index: int) -> str:
    return f"page-{index:03d}.json"
