"""Raw records -> contract ``Message`` objects.

Decisions (also in ARCHITECTURE.md):

* **System messages are dropped.** Only ordinary messages and the subtypes a
  person (or bot) authors are kept -- see ``KEPT_SUBTYPES``. Joins, topic
  changes, pins and the like are noise for triage.
* **"Its thread"** for ``awaiting_reply``: a message with ``thread_ts`` belongs
  to that thread; an unthreaded message belongs to its conversation's
  top-level stream. The last message of that group, by ``ts``, across *every*
  record in the raw dir (including ones older than the window), decides.
* **"It DMs or mentions me"**: the message is in a dm/mpim and is not mine, or
  its raw text contains ``<@ME>``. My own messages are never awaiting a reply.
* **is_dm** is true for both ``dm`` and ``mpim`` containers (a group DM is a DM).
* **is_mention** is a direct user mention only; ``@here``/``@channel`` do not count.
* **Window**: when the manifest records ``oldest``, only messages at or after it
  are emitted; older ones still inform ``awaiting_reply``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Author, Container, ContainerKind, Message
from .fields import build_permalink, mpim_readable_name, thread_ts_from_permalink, ts_key, ts_to_iso
from .mrkdwn import render
from .snapshot import ORIGIN_RANK, RawRecord, Snapshot

KEPT_SUBTYPES = frozenset({None, "", "bot_message", "thread_broadcast", "file_share", "me_message"})


@dataclass(frozen=True)
class _Resolved:
    channel: str
    ts: str
    thread_ts: str | None
    author_id: str
    author_name: str
    raw_text: str
    permalink: str | None


def _message_text(msg: dict[str, Any]) -> str:
    text = msg.get("text")
    if isinstance(text, str) and text:
        return text
    for attachment in msg.get("attachments") or []:
        if isinstance(attachment, dict):
            for key in ("fallback", "text", "pretext"):
                value = attachment.get(key)
                if isinstance(value, str) and value:
                    return value
    return ""


def _author(msg: dict[str, Any], users: dict[str, str]) -> tuple[str, str]:
    user_id = msg.get("user")
    if isinstance(user_id, str) and user_id:
        return user_id, users.get(user_id) or msg.get("username") or user_id
    bot_id = msg.get("bot_id")
    bot_profile = msg.get("bot_profile") if isinstance(msg.get("bot_profile"), dict) else {}
    name = (bot_profile or {}).get("name") or msg.get("username")
    if isinstance(bot_id, str) and bot_id:
        return bot_id, str(name or bot_id)
    return "unknown", str(name or "unknown")


def _resolve(record: RawRecord, users: dict[str, str]) -> _Resolved:
    msg = record.message
    ts = str(msg["ts"])
    permalink = msg.get("permalink") if isinstance(msg.get("permalink"), str) else None
    thread_ts = msg.get("thread_ts")
    if not thread_ts and permalink:
        thread_ts = thread_ts_from_permalink(permalink)
    author_id, author_name = _author(msg, users)
    return _Resolved(
        channel=record.channel,
        ts=ts,
        thread_ts=str(thread_ts) if thread_ts else None,
        author_id=author_id,
        author_name=author_name,
        raw_text=_message_text(msg),
        permalink=permalink,
    )


def _container(channel: str, snap: Snapshot, search_channel: dict[str, Any] | None) -> Container:
    conv = snap.conversations.get(channel)
    if conv is None and search_channel:
        conv = search_channel
    conv = conv or {}

    kind: ContainerKind
    if conv.get("is_im") or channel.startswith("D"):
        kind = "dm"
        other = conv.get("user")
        if not other and isinstance(conv.get("name"), str) and conv["name"] in snap.users:
            # search.messages names an IM by the other person's user id.
            other = conv["name"]
        name = f"@{snap.users.get(str(other), str(other))}" if other else channel
    elif conv.get("is_mpim") or str(conv.get("name", "")).startswith("mpdm-"):
        kind = "mpim"
        name = mpim_readable_name(str(conv.get("name") or channel))
    else:
        kind = "channel"
        name = f"#{conv['name']}" if conv.get("name") else channel
    return Container(kind=kind, id=channel, name=name)


def _channel_names(snap: Snapshot) -> dict[str, str]:
    return {
        cid: str(conv["name"])
        for cid, conv in snap.conversations.items()
        if conv.get("name") and not conv.get("is_im")
    }


def build_messages(snap: Snapshot) -> list[Message]:
    # 1. Keep ordinary messages; dedupe by (channel, ts). The richest origin
    #    supplies the body; a search match's permalink is kept if one exists.
    grouped: dict[tuple[str, str], list[RawRecord]] = {}
    search_channels: dict[str, dict[str, Any]] = {}
    for record in snap.records:
        if record.message.get("subtype") not in KEPT_SUBTYPES:
            continue
        if record.search_channel:
            search_channels.setdefault(record.channel, record.search_channel)
        grouped.setdefault((record.channel, str(record.message["ts"])), []).append(record)

    best: list[RawRecord] = []
    for records in grouped.values():
        chosen = min(records, key=lambda r: ORIGIN_RANK[r.origin])
        permalink = next(
            (
                r.message["permalink"]
                for r in records
                if isinstance(r.message.get("permalink"), str)
            ),
            None,
        )
        if permalink and "permalink" not in chosen.message:
            chosen = RawRecord(
                chosen.channel, {**chosen.message, "permalink": permalink}, chosen.origin
            )
        best.append(chosen)

    resolved = [_resolve(rec, snap.users) for rec in best]

    # 2. Last author per thread group, across everything (window ignored).
    last_in_group: dict[tuple[str, str], tuple[tuple[int, int], str]] = {}
    for res in resolved:
        group = (res.channel, res.thread_ts or "main")
        key = ts_key(res.ts)
        if group not in last_in_group or key > last_in_group[group][0]:
            last_in_group[group] = (key, res.author_id)

    # 3. Emit what falls inside the window.
    oldest = snap.manifest.get("oldest")
    oldest_key = ts_key(str(oldest)) if oldest else None
    channel_names = _channel_names(snap)
    mention_token = f"<@{snap.me}"

    out: list[Message] = []
    for res in resolved:
        if oldest_key is not None and ts_key(res.ts) < oldest_key:
            continue
        container = _container(res.channel, snap, search_channels.get(res.channel))
        mine = res.author_id == snap.me
        is_dm = container.kind in ("dm", "mpim")
        is_mention = not mine and mention_token in res.raw_text
        last_author = last_in_group[(res.channel, res.thread_ts or "main")][1]
        awaiting = (not mine) and (is_dm or is_mention) and last_author != snap.me
        out.append(
            Message(
                id=f"{res.channel}:{res.ts}",
                thread_id=f"{res.channel}:{res.thread_ts}" if res.thread_ts else None,
                container=container,
                author=Author(id=res.author_id, name=res.author_name),
                ts=ts_to_iso(res.ts),
                text=render(res.raw_text, snap.users, channel_names),
                permalink=res.permalink
                or build_permalink(snap.base_url, res.channel, res.ts, res.thread_ts),
                is_mention=is_mention,
                is_dm=is_dm,
                awaiting_reply=awaiting,
            )
        )

    out.sort(key=lambda m: (ts_key(m.id.rpartition(":")[2]), m.id))
    return out
