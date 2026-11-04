"""Task summaries for `task`, `mine` and `search` (not contract Messages)."""

from __future__ import annotations

from typing import Any

from .text import iso_from_ms, parse_ms


def task_summary(task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("id", ""))
    updated = parse_ms(task.get("date_updated"))
    return {
        "id": task_id,
        "name": str(task.get("name") or ""),
        "status": str((task.get("status") or {}).get("status") or ""),
        "url": str(task.get("url") or f"https://app.clickup.com/t/{task_id}"),
        "list": str((task.get("list") or {}).get("name") or ""),
        "assignees": [
            str(a.get("username") or a.get("id") or "") for a in task.get("assignees") or []
        ],
        "date_updated": iso_from_ms(updated) if updated is not None else None,
    }


def task_matches(task: dict[str, Any], query: str) -> bool:
    """Every whitespace-separated term appears (case-insensitive) in the task's
    name, custom id or plain-text description."""
    haystack = " ".join(
        str(task.get(key) or "") for key in ("name", "custom_id", "text_content", "description")
    ).lower()
    terms = query.lower().split()
    return bool(terms) and all(term in haystack for term in terms)
