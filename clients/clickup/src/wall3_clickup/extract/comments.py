"""Task comments + their threaded replies, written verbatim."""

from __future__ import annotations

from ..client import COMMENT_PAGE_SIZE, ClickUpClient
from .common import page_name
from .raw_writer import RawWriter, safe_component


def write_task_comments(
    client: ClickUpClient,
    writer: RawWriter,
    task_id: str,
    *,
    max_pages: int,
    stop_before_ms: int | None = None,
) -> list[str]:
    """Write `comments/<task_id>/page-NNN.json` and `replies/<comment_id>.json`.

    Replies are fetched for every comment with `reply_count` > 0 (the field is
    a string in the documented response). Returns notes for the manifest.
    """
    safe_component(task_id)
    notes: list[str] = []
    pages = client.iter_task_comment_pages(
        task_id, max_pages=max_pages, stop_before_ms=stop_before_ms
    )
    count = 0
    last_full = False
    for index, page in enumerate(pages):
        writer.write(f"comments/{task_id}/{page_name(index)}", page)
        count = index + 1
        last_full = len(page.get("comments") or []) >= COMMENT_PAGE_SIZE
        for comment in page.get("comments") or []:
            try:
                replies = int(comment.get("reply_count") or 0)
            except (TypeError, ValueError):
                replies = 0
            if replies > 0:
                comment_id = safe_component(str(comment["id"]))
                writer.write(f"replies/{comment_id}.json", client.get_comment_replies(comment_id))
    if count == max_pages and last_full:
        notes.append(f"comments for task {task_id} stopped at the {max_pages}-page cap")
    return notes
