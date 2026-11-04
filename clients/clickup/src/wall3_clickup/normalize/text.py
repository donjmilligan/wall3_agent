"""Timestamps, comment rendering and @name mention detection."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


def parse_ms(value: Any) -> int | None:
    """ClickUp sends Unix-epoch milliseconds as strings (v2) or numbers (v3)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def tag_user_ids(comment: dict[str, Any]) -> set[str]:
    """User ids @-tagged in a task comment's structured `comment` items.

    A tag item is `{"type": "tag", "user": {"id": ...}}`
    (https://developer.clickup.com/docs/comment-formatting#tag-people-in-task-comments).
    """
    ids: set[str] = set()
    for item in comment.get("comment") or []:
        if not isinstance(item, dict) or item.get("type") != "tag":
            continue
        user = item.get("user") or {}
        if user.get("id") is not None:
            ids.add(str(user["id"]))
    return ids


def render_comment(comment: dict[str, Any], users: dict[str, str]) -> str:
    """Plain text of a task comment, with tag items rendered as `@name`."""
    items = comment.get("comment")
    if not isinstance(items, list) or not items:
        return str(comment.get("comment_text") or "")
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tag":
            user = item.get("user") or {}
            uid = str(user.get("id", ""))
            name = user.get("username") or users.get(uid)
            text = str(item.get("text") or "")
            if name:
                parts.append(f"@{name}")
            elif text.startswith("@"):
                parts.append(text)
            else:
                parts.append(f"@{uid}" if uid else text)
        else:
            parts.append(str(item.get("text") or ""))
    return "".join(parts)


def mentions_name(text: str, name: str) -> bool:
    """`@<full name>` in text, case-insensitive, not part of a longer word.

    Only the full display name counts: a bare first name would match every
    other person who shares it.
    """
    if not name:
        return False
    pattern = r"(?<![\w@])@" + re.escape(name) + r"(?!\w)"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None
