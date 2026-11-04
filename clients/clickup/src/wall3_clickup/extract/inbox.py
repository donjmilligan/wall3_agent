"""`clickup inbox --since 24h`: everything that might need me, in one raw snapshot.

ClickUp's public API has no notifications/inbox, mentions or "watched tasks"
endpoint (verified against https://developer.clickup.com/llms.txt). The closest
honest approximation, and what this does:

1. Workspace tasks updated inside the window (GET /v2/team/{id}/task with
   `date_updated_gt`, closed tasks and subtasks included), newest first,
   capped at MAX_TASKS_FOR_COMMENTS.
2. For each, the comments inside the window (+ threaded replies).
3. Chat channels with a message inside the window (`with_message_since`),
   capped at MAX_CHANNELS, their recent messages, replies and tagged users.

normalize/ then decides which of those messages are mine to look at. Anything
capped or skipped is recorded in manifest.json `notes` and echoed by the CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..client import TASK_PAGE_SIZE, ClickUpAPIError, ClickUpClient
from .chat import write_channel_messages
from .comments import write_task_comments
from .common import page_name, write_identity, write_manifest
from .raw_writer import RawWriter

MAX_TASK_PAGES = 5  # 500 tasks
MAX_TASKS_FOR_COMMENTS = 100  # one+ request each; ~1 minute at 100 req/min
INBOX_COMMENT_PAGES = 4
MAX_CHANNEL_PAGES = 3
MAX_CHANNELS = 50
INBOX_MESSAGE_PAGES = 3


def _date(task: dict[str, Any]) -> int:
    try:
        return int(task.get("date_updated") or 0)
    except (TypeError, ValueError):
        return 0


def extract_inbox(client: ClickUpClient, writer: RawWriter, *, team_id: str, since_ms: int) -> Path:
    notes: list[str] = []
    me_id = write_identity(client, writer)

    tasks: list[dict[str, Any]] = []
    pages = 0
    last_full = False
    task_pages = client.iter_team_task_pages(
        team_id,
        max_pages=MAX_TASK_PAGES,
        date_updated_gt=since_ms,
        include_closed=True,
        subtasks=True,
    )
    for index, page in enumerate(task_pages):
        writer.write(f"task_pages/{page_name(index)}", page)
        batch = page.get("tasks") or []
        tasks.extend(batch)
        pages = index + 1
        last_full = len(batch) >= TASK_PAGE_SIZE
    if pages == MAX_TASK_PAGES and last_full:
        notes.append(f"task listing stopped at the {MAX_TASK_PAGES}-page cap")

    tasks.sort(key=_date, reverse=True)
    if len(tasks) > MAX_TASKS_FOR_COMMENTS:
        notes.append(
            f"{len(tasks)} tasks updated in the window; comments read for the "
            f"{MAX_TASKS_FOR_COMMENTS} most recently updated only"
        )
    for task in tasks[:MAX_TASKS_FOR_COMMENTS]:
        notes += write_task_comments(
            client,
            writer,
            str(task["id"]),
            max_pages=INBOX_COMMENT_PAGES,
            stop_before_ms=since_ms,
        )

    try:
        channels: list[dict[str, Any]] = []
        channel_pages = client.iter_chat_channel_pages(
            team_id, max_pages=MAX_CHANNEL_PAGES, with_message_since=since_ms
        )
        for index, page in enumerate(channel_pages):
            writer.write(f"chat/channels/{page_name(index)}", page)
            channels.extend(page.get("data") or [])
        if len(channels) > MAX_CHANNELS:
            notes.append(
                f"{len(channels)} chat channels active in the window; "
                f"messages read for the first {MAX_CHANNELS} only"
            )
        for channel in channels[:MAX_CHANNELS]:
            notes += write_channel_messages(
                client,
                writer,
                workspace_id=team_id,
                channel_id=str(channel["id"]),
                me_id=me_id,
                max_pages=INBOX_MESSAGE_PAGES,
                since_ms=since_ms,
            )
    except ClickUpAPIError as exc:
        # The Chat API is experimental; a workspace without it must not lose
        # the task-comment half of the inbox. Loud, not silent: the CLI prints
        # every note to stderr.
        notes.append(f"chat skipped: {exc}")

    write_manifest(writer, kind="inbox", team_id=team_id, since_ms=since_ms, notes=notes)
    return writer.directory
