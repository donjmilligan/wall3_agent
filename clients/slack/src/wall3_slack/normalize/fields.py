"""Small, pure field conversions: timestamps, permalinks, names."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse


def ts_key(ts: str) -> tuple[int, int]:
    """An exact sort key for a Slack ``ts`` ("1700000000.000100").

    Compared as integers, not floats, so two messages a microsecond apart
    never compare equal.
    """
    seconds, _, fraction = ts.partition(".")
    return int(seconds), int((fraction or "0").ljust(6, "0")[:6])


def ts_to_iso(ts: str) -> str:
    """Slack ``ts`` -> ISO-8601 UTC with second precision, e.g. 2026-11-04T14:03:22Z."""
    seconds = int(ts.partition(".")[0])
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_permalink(base_url: str, channel: str, ts: str, thread_ts: str | None) -> str:
    """Construct a message permalink without an API call.

    Format documented at docs.slack.dev/reference/methods/chat.getPermalink:
    ``<workspace url>archives/<channel>/p<ts without the dot>``; a reply adds
    ``?thread_ts=<parent ts>&cid=<channel>``. ``base_url`` is auth.test's
    ``url`` (e.g. ``https://example.slack.com/``).
    """
    root = base_url if base_url.endswith("/") else base_url + "/"
    link = f"{root}archives/{channel}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={channel}"
    return link


def thread_ts_from_permalink(permalink: str) -> str | None:
    """search.messages matches carry no ``thread_ts``; their permalink does."""
    values = parse_qs(urlparse(permalink).query).get("thread_ts")
    return values[0] if values else None


def user_display_name(user: dict[str, Any]) -> str:
    """users.info ``user`` object -> the name Slack shows for that person."""
    profile = user.get("profile") or {}
    for candidate in (
        profile.get("display_name"),
        profile.get("real_name"),
        user.get("real_name"),
        user.get("name"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return str(user.get("id", ""))


def mpim_readable_name(raw_name: str) -> str:
    """``mpdm-alice--bob--don-1`` -> ``alice, bob, don`` (Slack's mpim naming)."""
    name = raw_name
    if name.startswith("mpdm-"):
        name = name[len("mpdm-") :]
        head, sep, tail = name.rpartition("-")
        if sep and tail.isdigit():
            name = head
        return ", ".join(part for part in name.split("--") if part)
    return raw_name
