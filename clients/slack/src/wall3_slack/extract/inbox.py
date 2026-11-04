"""``inbox --since``: everything that may need my attention in a window.

Four sources, each capped (see ``InboxLimits``) so a busy workspace cannot fan
out into an unbounded number of per-conversation calls:

1. **DMs and group DMs** -- ``conversations.list types=im,mpim`` (Tier 2), then
   ``conversations.history oldest=<since>`` (Tier 3) for at most
   ``max_conversations`` of them, most recently updated first. Slack exposes
   no reliable "last message" field on conversation objects, so this is an
   ordering heuristic; anything past the cap is reported as truncated.
2. **@-mentions of me** -- ``search.messages`` (Tier 2) with
   ``<@ME> after:<date>``, newest first, stopping once results predate the
   window. ``after:`` is day-granular, so the date is padded back two days
   and the exact cut happens at normalize time.
3. **Threads I'm in that got new replies** -- Slack's API has no "my threads"
   view. We approximate: ``search.messages from:<@ME>`` over a lookback
   window finds threads I posted in, plus DM thread parents whose
   ``latest_reply`` is inside the window.
4. **Thread bodies** -- ``conversations.replies`` (Tier 3) for each thread
   found by 2 and 3, up to ``max_threads``, so ``awaiting_reply`` can see who
   spoke last.

Every response is written verbatim; ``manifest.json`` records the window,
call counts, and everything truncated or skipped.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..client import SlackClient, next_cursor
from .common import (
    RunRecord,
    fetch_history,
    fetch_replies,
    fetch_users,
    referenced_user_ids,
    write_whoami,
)
from .store import RawStore

MY_MESSAGES_LABEL = "search.messages/my-messages"


@dataclass(frozen=True)
class InboxLimits:
    max_list_pages: int = 10
    max_conversations: int = 50
    max_history_pages: int = 3
    max_search_pages: int = 3
    max_threads: int = 40
    thread_lookback_days: int = 7
    max_users: int = 200


def _activity_hint(conv: dict[str, Any]) -> float:
    """Best-effort recency for ordering only. ``updated`` is in ms in Slack's examples."""
    for key in ("updated", "created"):
        value = conv.get(key)
        if isinstance(value, int | float) and value > 0:
            return value / 1000.0 if value > 1e11 else float(value)
    return 0.0


def _ts_float(ts: Any) -> float:
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def _thread_of_match(match: dict[str, Any]) -> tuple[str, str] | None:
    channel = (match.get("channel") or {}).get("id")
    ts = match.get("ts")
    if not channel or not ts:
        return None
    permalink = match.get("permalink") or ""
    thread_ts = parse_qs(urlparse(permalink).query).get("thread_ts")
    return str(channel), str(thread_ts[0] if thread_ts else ts)


def _search_pages(
    client: SlackClient,
    store: RawStore,
    run: RunRecord,
    *,
    label: str,
    query: str,
    stop_before: float,
    max_pages: int,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for page_no in range(1, max_pages + 1):
        body = client.search_messages(query, sort="timestamp", sort_dir="desc", page=page_no)
        run.count("search.messages")
        store.write(f"search.messages/{label}/page-{page_no:03d}.json", body)
        block = body.get("messages") or {}
        page_matches = [m for m in block.get("matches") or [] if isinstance(m, dict)]
        matches.extend(page_matches)
        pages = int((block.get("paging") or {}).get("pages") or 1)
        if page_no >= pages:
            return matches
        if page_matches and _ts_float(page_matches[-1].get("ts")) < stop_before:
            return matches  # sorted newest-first: the rest predates the window
    run.truncated.append(f"search.messages {label}: stopped after {max_pages} page(s)")
    return matches


def extract_inbox(
    client: SlackClient,
    out_dir: Path,
    *,
    oldest: datetime,
    now: datetime,
    since_label: str,
    limits: InboxLimits | None = None,
) -> Path:
    limits = limits or InboxLimits()
    store = RawStore(out_dir)
    oldest_ts = f"{oldest.timestamp():.6f}"
    run = RunRecord(
        verb="inbox",
        fetched_at=now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        oldest=oldest_ts,
        since=since_label,
        discovery_only=[MY_MESSAGES_LABEL],
        params=asdict(limits),
    )

    whoami = write_whoami(client, store, run)
    me = str(whoami["user_id"])
    all_messages: list[dict[str, Any]] = []

    # 1. DMs and group DMs.
    conversations: list[dict[str, Any]] = []
    last_page: dict[str, Any] = {}
    for i, page in enumerate(
        client.paginate(
            "conversations.list",
            {"types": "im,mpim", "exclude_archived": "true", "limit": 200},
            max_pages=limits.max_list_pages,
        ),
        start=1,
    ):
        run.count("conversations.list")
        store.write(f"conversations.list/im-mpim-page-{i:03d}.json", page)
        conversations.extend(c for c in page.get("channels") or [] if isinstance(c, dict))
        last_page = page
    if next_cursor(last_page):
        run.truncated.append(
            f"conversations.list: more than {limits.max_list_pages} page(s) of DMs; the rest "
            "were not listed"
        )

    candidates = [
        c for c in conversations if not c.get("is_user_deleted") and not c.get("is_archived")
    ]
    candidates.sort(key=_activity_hint, reverse=True)
    if len(candidates) > limits.max_conversations:
        run.truncated.append(
            f"DMs: {len(candidates) - limits.max_conversations} of {len(candidates)} "
            f"conversations beyond the cap of {limits.max_conversations} were not read"
        )

    threads: list[tuple[str, str]] = []
    for conv in candidates[: limits.max_conversations]:
        channel = str(conv["id"])
        history = fetch_history(
            client, store, run, channel, oldest=oldest_ts, max_pages=limits.max_history_pages
        )
        all_messages.extend(history)
        for msg in history:
            if msg.get("reply_count") and _ts_float(msg.get("latest_reply")) >= float(oldest_ts):
                threads.append((channel, str(msg.get("thread_ts") or msg["ts"])))

    # 2. Mentions of me.
    pad = (oldest - timedelta(days=2)).date().isoformat()
    mention_matches = _search_pages(
        client,
        store,
        run,
        label="mentions",
        query=f"<@{me}> after:{pad}",
        stop_before=float(oldest_ts),
        max_pages=limits.max_search_pages,
    )
    all_messages.extend(mention_matches)
    mention_threads = [t for m in mention_matches if (t := _thread_of_match(m))]

    # 3. Threads I posted in (discovery only -- these matches are not emitted).
    lookback = oldest - timedelta(days=limits.thread_lookback_days)
    mine = _search_pages(
        client,
        store,
        run,
        label="my-messages",
        query=f"from:<@{me}> after:{(lookback - timedelta(days=1)).date().isoformat()}",
        stop_before=lookback.timestamp(),
        max_pages=limits.max_search_pages,
    )
    my_threads = [
        t for m in mine if (t := _thread_of_match(m)) and not t[0].startswith("D")
    ]  # top-level DMs are already covered by history

    # 4. Thread bodies: mentions first (most likely to need me), then DM threads, then mine.
    ordered: list[tuple[str, str]] = []
    for target in [*mention_threads, *threads, *my_threads]:
        if target not in ordered:
            ordered.append(target)
    if len(ordered) > limits.max_threads:
        run.truncated.append(
            f"threads: {len(ordered) - limits.max_threads} of {len(ordered)} threads beyond "
            f"the cap of {limits.max_threads} were not read"
        )
    for channel, ts in ordered[: limits.max_threads]:
        all_messages.extend(fetch_replies(client, store, run, channel, ts))

    # Users referenced anywhere, so normalize can render names offline.
    user_ids = referenced_user_ids(all_messages, conversations)
    if me not in user_ids:
        user_ids.insert(0, me)
    fetch_users(client, store, run, user_ids, max_users=limits.max_users)

    store.write("manifest.json", run.to_dict())
    return out_dir
