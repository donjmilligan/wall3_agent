"""v3 Chat extraction: channel messages, thread replies, tagged users.

Tagged-user lookups cost one request per message, so they are made only for
messages that could be a mention of me: not authored by me, and containing an
`@` in their plain-text content. A mention rendered without an `@` would be
missed -- see ARCHITECTURE.md, known limits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..client import ClickUpClient
from .common import page_name, write_identity, write_manifest
from .raw_writer import RawWriter, safe_component

CHANNEL_MESSAGE_PAGES = 2  # 100 messages for `chat messages <id>`
REPLY_PAGES = 2


def _might_mention_me(message: dict[str, Any], me_id: str) -> bool:
    return str(message.get("user_id", "")) != me_id and "@" in str(message.get("content", ""))


def write_channel_messages(
    client: ClickUpClient,
    writer: RawWriter,
    *,
    workspace_id: str,
    channel_id: str,
    me_id: str,
    max_pages: int,
    since_ms: int | None = None,
) -> list[str]:
    """Write `chat/messages/<channel>/page-NNN.json`, replies and tagged users.

    With `since_ms`, paging stops once a page reaches messages older than the
    window (assumes newest-first ordering, which the docs do not state), and
    tagged-user lookups are limited to messages inside the window.
    """
    safe_component(channel_id)
    notes: list[str] = []
    messages: list[dict[str, Any]] = []
    pages = client.iter_chat_message_pages(workspace_id, channel_id, max_pages=max_pages)
    for index, page in enumerate(pages):
        writer.write(f"chat/messages/{channel_id}/{page_name(index)}", page)
        batch = page.get("data") or []
        messages.extend(batch)
        if since_ms is not None and batch:
            if min(float(m.get("date") or 0) for m in batch) < since_ms:
                break
    candidates: list[dict[str, Any]] = []
    for message in messages:
        candidates.append(message)
        if float(message.get("replies_count") or 0) > 0:
            message_id = safe_component(str(message["id"]))
            reply_pages = client.iter_chat_reply_pages(
                workspace_id, message_id, max_pages=REPLY_PAGES
            )
            for index, page in enumerate(reply_pages):
                writer.write(f"chat/replies/{message_id}/{page_name(index)}", page)
                candidates.extend(page.get("data") or [])
    for message in candidates:
        if since_ms is not None and float(message.get("date") or 0) < since_ms:
            continue
        if _might_mention_me(message, me_id):
            message_id = safe_component(str(message["id"]))
            writer.write(
                f"chat/tagged_users/{message_id}.json",
                client.get_chat_tagged_users(workspace_id, message_id),
            )
    return notes


def extract_channel(
    client: ClickUpClient, writer: RawWriter, *, team_id: str, channel_id: str
) -> Path:
    """`clickup chat messages <channel_id>`: the latest messages in one channel."""
    safe_component(channel_id)
    me_id = write_identity(client, writer)
    writer.write(f"chat/channel/{channel_id}.json", client.get_chat_channel(team_id, channel_id))
    notes = write_channel_messages(
        client,
        writer,
        workspace_id=team_id,
        channel_id=channel_id,
        me_id=me_id,
        max_pages=CHANNEL_MESSAGE_PAGES,
    )
    write_manifest(writer, kind="chat", team_id=team_id, since_ms=None, notes=notes)
    return writer.directory
