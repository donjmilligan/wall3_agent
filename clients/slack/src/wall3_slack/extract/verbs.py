"""Extract for the single-target verbs: ``thread``, ``channel``, ``search``, ``user``."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..client import SlackAPIError, SlackAuthError, SlackClient, next_cursor
from .common import (
    RunRecord,
    fetch_history,
    fetch_replies,
    fetch_users,
    referenced_user_ids,
    write_whoami,
)
from .store import RawStore, safe_component

CHANNEL_ID = re.compile(r"^[CGD][A-Z0-9]{2,}$")
USER_ID = re.compile(r"^[UW][A-Z0-9]{2,}$")
MAX_USERS = 200


class NotFoundError(LookupError):
    """A channel or user name that does not resolve."""


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_info(client: SlackClient, store: RawStore, run: RunRecord, channel: str) -> None:
    """conversations.info, for a readable container name. Missing it is not fatal."""
    safe_component(channel)  # becomes a path component below; refuse before any call
    try:
        body = client.conversations_info(channel)
    except SlackAuthError:
        raise
    except SlackAPIError as exc:
        run.skipped.append(f"conversations.info {channel}: {exc.error}")
        return
    run.count("conversations.info")
    store.write(f"conversations.info/{safe_component(channel)}.json", body)


def extract_thread(
    client: SlackClient, out_dir: Path, *, channel: str, ts: str, now: datetime
) -> Path:
    safe_component(channel)  # both become path components; refuse before any call
    safe_component(ts)
    store = RawStore(out_dir)
    run = RunRecord(verb="thread", fetched_at=_stamp(now), params={"channel": channel, "ts": ts})
    write_whoami(client, store, run)
    _fetch_info(client, store, run, channel)
    messages = fetch_replies(client, store, run, channel, ts, max_pages=10)
    fetch_users(client, store, run, referenced_user_ids(messages), max_users=MAX_USERS)
    store.write("manifest.json", run.to_dict())
    return out_dir


def _resolve_channel(
    client: SlackClient, store: RawStore, run: RunRecord, ref: str, *, max_pages: int = 20
) -> str:
    if CHANNEL_ID.match(ref):
        return ref
    name = ref.lstrip("#")
    last: dict[str, Any] = {}
    for i, page in enumerate(
        client.paginate(
            "conversations.list",
            {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 200},
            max_pages=max_pages,
        ),
        start=1,
    ):
        run.count("conversations.list")
        store.write(f"conversations.list/channels-page-{i:03d}.json", page)
        for conv in page.get("channels") or []:
            if isinstance(conv, dict) and conv.get("name") == name:
                return str(conv["id"])
        last = page
    more = " (listing truncated; pass the channel id instead)" if next_cursor(last) else ""
    raise NotFoundError(f"no channel named {name!r} visible to this token{more}")


def extract_channel(
    client: SlackClient,
    out_dir: Path,
    *,
    ref: str,
    oldest: datetime | None,
    limit: int,
    now: datetime,
) -> Path:
    store = RawStore(out_dir)
    oldest_ts = f"{oldest.timestamp():.6f}" if oldest else None
    run = RunRecord(
        verb="channel",
        fetched_at=_stamp(now),
        oldest=oldest_ts,
        params={"ref": ref, "limit": limit},
    )
    write_whoami(client, store, run)
    channel = _resolve_channel(client, store, run, ref)
    run.params["channel"] = channel
    _fetch_info(client, store, run, channel)
    page_limit = max(1, min(limit, 200))
    max_pages = max(1, -(-limit // page_limit))
    messages = fetch_history(
        client, store, run, channel, oldest=oldest_ts, max_pages=max_pages, page_limit=page_limit
    )
    fetch_users(client, store, run, referenced_user_ids(messages), max_users=MAX_USERS)
    store.write("manifest.json", run.to_dict())
    return out_dir


def extract_search(
    client: SlackClient, out_dir: Path, *, query: str, count: int, now: datetime
) -> Path:
    store = RawStore(out_dir)
    run = RunRecord(verb="search", fetched_at=_stamp(now), params={"query": query, "count": count})
    write_whoami(client, store, run)
    body = client.search_messages(query, sort="timestamp", sort_dir="desc", count=min(count, 100))
    run.count("search.messages")
    store.write("search.messages/query/page-001.json", body)
    block = body.get("messages") or {}
    matches = [m for m in block.get("matches") or [] if isinstance(m, dict)]
    if int((block.get("paging") or {}).get("pages") or 1) > 1:
        total = (block.get("paging") or {}).get("total")
        run.truncated.append(f"search.messages: showing {len(matches)} of {total} matches")
    fetch_users(client, store, run, referenced_user_ids(matches), max_users=MAX_USERS)
    store.write("manifest.json", run.to_dict())
    return out_dir


def lookup_user(client: SlackClient, ref: str, *, max_pages: int = 20) -> dict[str, Any]:
    """users.info for an id, or a users.list scan for ``@name`` (handle, display or real name)."""
    if USER_ID.match(ref):
        user = client.users_info(ref).get("user")
        if not isinstance(user, dict):
            raise NotFoundError(f"users.info returned no user for {ref!r}")
        return user
    wanted = ref.lstrip("@").casefold()
    for page in client.paginate("users.list", {"limit": 200}, max_pages=max_pages):
        for user in page.get("members") or []:
            if not isinstance(user, dict):
                continue
            profile = user.get("profile") or {}
            names = (
                user.get("name"),
                user.get("real_name"),
                profile.get("display_name"),
                profile.get("real_name"),
            )
            if any(isinstance(n, str) and n.casefold() == wanted for n in names):
                return user
    raise NotFoundError(f"no user matching {ref!r}")
