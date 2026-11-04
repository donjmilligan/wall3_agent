"""Fetch helpers shared by every extract verb. Network only; no transformation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..client import SlackAPIError, SlackAuthError, SlackClient, next_cursor
from .store import RawStore

_MENTION = re.compile(r"<@([UW][A-Z0-9]+)")


@dataclass
class RunRecord:
    """What a run did and, crucially, what it did *not* fetch."""

    verb: str
    fetched_at: str
    oldest: str | None = None
    since: str | None = None
    discovery_only: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    calls: dict[str, int] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def count(self, method: str, n: int = 1) -> None:
        self.calls[method] = self.calls.get(method, 0) + n

    def to_dict(self) -> dict[str, Any]:
        return {
            "verb": self.verb,
            "fetched_at": self.fetched_at,
            "since": self.since,
            "oldest": self.oldest,
            "params": self.params,
            "discovery_only": self.discovery_only,
            "truncated": self.truncated,
            "skipped": self.skipped,
            "calls": self.calls,
        }


def write_whoami(client: SlackClient, store: RawStore, run: RunRecord) -> dict[str, Any]:
    whoami = client.auth_test()
    run.count("auth.test")
    store.write("whoami.json", whoami)
    return whoami


def fetch_history(
    client: SlackClient,
    store: RawStore,
    run: RunRecord,
    channel: str,
    *,
    oldest: str | None,
    max_pages: int,
    page_limit: int = 200,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for i, page in enumerate(
        client.paginate(
            "conversations.history",
            {"channel": channel, "oldest": oldest, "limit": page_limit},
            max_pages=max_pages,
        ),
        start=1,
    ):
        run.count("conversations.history")
        store.write(f"conversations.history/{channel}/page-{i:03d}.json", page)
        messages.extend(m for m in page.get("messages") or [] if isinstance(m, dict))
        last = page
    if next_cursor(last):
        run.truncated.append(
            f"conversations.history {channel}: stopped after {max_pages} page(s); older "
            "messages in the window were not fetched"
        )
    return messages


def fetch_replies(
    client: SlackClient,
    store: RawStore,
    run: RunRecord,
    channel: str,
    ts: str,
    *,
    max_pages: int = 1,
) -> list[dict[str, Any]]:
    """Fetch one whole thread. ``thread_not_found`` and friends are skipped, not fatal."""
    messages: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    try:
        for i, page in enumerate(
            client.paginate(
                "conversations.replies",
                {"channel": channel, "ts": ts, "limit": 200},
                max_pages=max_pages,
            ),
            start=1,
        ):
            run.count("conversations.replies")
            store.write(f"conversations.replies/{channel}-{ts}/page-{i:03d}.json", page)
            messages.extend(m for m in page.get("messages") or [] if isinstance(m, dict))
            last = page
    except SlackAuthError:
        raise
    except SlackAPIError as exc:
        if exc.error in ("thread_not_found", "channel_not_found", "message_not_found"):
            run.skipped.append(f"conversations.replies {channel} {ts}: {exc.error}")
            return messages
        raise
    if next_cursor(last):
        run.truncated.append(
            f"conversations.replies {channel} {ts}: thread longer than {max_pages} page(s)"
        )
    return messages


def referenced_user_ids(
    messages: Iterable[dict[str, Any]], conversations: Iterable[dict[str, Any]] = ()
) -> list[str]:
    """Every user id an author, a mention, or a DM partner refers to, in first-seen order."""
    seen: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.append(value)

    for conv in conversations:
        add(conv.get("user"))
    for msg in messages:
        add(msg.get("user"))
        for mentioned in _MENTION.findall(str(msg.get("text") or "")):
            add(mentioned)
    return seen


def fetch_users(
    client: SlackClient,
    store: RawStore,
    run: RunRecord,
    user_ids: list[str],
    *,
    max_users: int,
) -> None:
    for user_id in user_ids[:max_users]:
        try:
            body = client.users_info(user_id)
        except SlackAuthError:
            raise
        except SlackAPIError as exc:
            run.skipped.append(f"users.info {user_id}: {exc.error}")
            continue
        run.count("users.info")
        store.write(f"users.info/{user_id}.json", body)
    if len(user_ids) > max_users:
        run.truncated.append(
            f"users.info: {len(user_ids) - max_users} user(s) beyond the cap of {max_users} "
            "were not resolved; their mentions render as raw ids"
        )
