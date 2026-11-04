"""`clickup task <id> [--comments]`: one task, optionally its full comment history."""

from __future__ import annotations

from pathlib import Path

from ..client import ClickUpClient
from .comments import write_task_comments
from .common import write_identity, write_manifest
from .raw_writer import RawWriter, safe_component

TASK_COMMENT_PAGES = 10  # 250 comments


def extract_task(
    client: ClickUpClient, writer: RawWriter, *, task_id: str, team_id: str, with_comments: bool
) -> Path:
    safe_component(task_id)
    notes: list[str] = []
    writer.write(f"tasks/{task_id}.json", client.get_task(task_id))
    if with_comments:
        write_identity(client, writer)
        notes += write_task_comments(client, writer, task_id, max_pages=TASK_COMMENT_PAGES)
    write_manifest(writer, kind="task", team_id=team_id, since_ms=None, notes=notes)
    return writer.directory
