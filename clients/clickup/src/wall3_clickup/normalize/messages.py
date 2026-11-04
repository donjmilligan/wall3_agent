"""Raw snapshot directory -> contract Messages.

Definitions (also in ARCHITECTURE.md):

* **thread** -- task comments: every comment and threaded reply on the task
  (`thread_id` = task id). Chat: a reply and its root share the root's id; a
  top-level chat message with no replies has `thread_id` null, and for
  `awaiting_reply` its "thread" is the channel's top-level stream.
* **is_mention** -- someone other than me @-tagged me: a structured tag item
  with my user id (task comments), my id in the message's tagged users (chat),
  or `@<my full name>` in the text. A message I wrote is never a mention.
* **is_dm** -- the chat channel is a DM or group DM. Task comments: false.
* **awaiting_reply** -- not written by me, DMs or mentions me, and the latest
  message in its thread is not mine.

For an `inbox` snapshot the output is restricted to what concerns me and to
the manifest's time window. *Concerns me*: task comments on tasks I am
assigned to, watch or created, or whose thread mentions me; chat messages in
DMs, that mention me, or whose reply thread mentions me. `task`/`chat`
snapshots are emitted whole -- the user asked for that container.
Messages outside the window still count when deciding the latest message in a
thread.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import Author, Container, ContainerKind, Message
from .text import iso_from_ms, mentions_name, parse_ms, render_comment, tag_user_ids

_CHANNEL_KIND: dict[str, ContainerKind] = {"CHANNEL": "chat", "DM": "dm", "GROUP_DM": "mpim"}
_DM_TYPES = frozenset({"DM", "GROUP_DM"})
UNKNOWN_USER = "unknown"


class NormalizeError(RuntimeError):
    """The snapshot cannot be normalized honestly (e.g. no whoami.json)."""


@dataclass
class NormalizeResult:
    messages: list[Message]
    warnings: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Draft:
    """A message before thread-level facts (awaiting, relevance) are known."""

    id: str
    ts_ms: int
    group: tuple[str, str]
    thread_id: str | None
    container: Container
    author: Author
    text: str
    permalink: str
    is_mention: bool
    is_dm: bool
    task_id: str | None = None


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _pages(directory: Path) -> list[Any]:
    if not directory.is_dir():
        return []
    return [_load(p) for p in sorted(directory.glob("*.json"))]


class _Snapshot:
    def __init__(self, raw_dir: Path) -> None:
        if not raw_dir.is_dir():
            raise NormalizeError(f"{raw_dir} is not a directory")
        whoami_path = raw_dir / "whoami.json"
        if not whoami_path.is_file():
            raise NormalizeError(
                f"{whoami_path} is missing; without it 'me' is unknown and "
                "is_mention/awaiting_reply cannot be computed"
            )
        self.dir = raw_dir
        manifest_path = raw_dir / "manifest.json"
        self.manifest: dict[str, Any] = _load(manifest_path) if manifest_path.is_file() else {}
        me = (_load(whoami_path).get("user")) or {}
        self.me_id = str(me.get("id", ""))
        self.me_name = str(me.get("username") or "")
        if not self.me_id:
            raise NormalizeError(f"{whoami_path} has no user.id")

        self.users: dict[str, str] = {self.me_id: self.me_name}
        teams_path = raw_dir / "teams.json"
        if teams_path.is_file():
            for team in _load(teams_path).get("teams") or []:
                for member in team.get("members") or []:
                    user = member.get("user") or {}
                    if user.get("id") is not None and user.get("username"):
                        self.users.setdefault(str(user["id"]), str(user["username"]))

        self.tasks: dict[str, dict[str, Any]] = {}
        for page in _pages(raw_dir / "task_pages"):
            for task in page.get("tasks") or []:
                self.tasks[str(task["id"])] = task
        for task in _pages(raw_dir / "tasks"):
            self.tasks[str(task["id"])] = task

        self.channels: dict[str, dict[str, Any]] = {}
        for page in _pages(raw_dir / "chat" / "channels"):
            for channel in page.get("data") or []:
                self.channels[str(channel["id"])] = channel
        for single in _pages(raw_dir / "chat" / "channel"):
            channel = single.get("data") or {}
            if channel.get("id") is not None:
                self.channels[str(channel["id"])] = channel

        self.tagged: dict[str, set[str]] = {}
        tagged_dir = raw_dir / "chat" / "tagged_users"
        if tagged_dir.is_dir():
            for path in sorted(tagged_dir.glob("*.json")):
                users = _load(path).get("data") or []
                self.tagged[path.stem] = {str(u.get("id")) for u in users}
                for u in users:
                    name = u.get("name") or u.get("username")
                    if u.get("id") is not None and name:
                        self.users.setdefault(str(u["id"]), str(name))

    def user_name(self, user_id: str, fallback: str | None = None) -> str:
        return self.users.get(user_id) or fallback or user_id


def _task_drafts(snap: _Snapshot, warnings: list[str]) -> list[_Draft]:
    drafts: list[_Draft] = []
    comment_task: dict[str, str] = {}
    comments_root = snap.dir / "comments"
    task_dirs: list[Path] = []
    if comments_root.is_dir():
        task_dirs = sorted(p for p in comments_root.iterdir() if p.is_dir())

    def build(comment: dict[str, Any], task_id: str) -> _Draft | None:
        comment_id = str(comment.get("id", ""))
        ts_ms = parse_ms(comment.get("date"))
        if not comment_id or ts_ms is None:
            warnings.append(f"task {task_id}: skipped a comment with no id or date")
            return None
        user = comment.get("user") or {}
        author_id = str(user["id"]) if user.get("id") is not None else UNKNOWN_USER
        author = Author(author_id, snap.user_name(author_id, user.get("username")))
        text = render_comment(comment, snap.users)
        mine = author_id == snap.me_id
        is_mention = not mine and (
            snap.me_id in tag_user_ids(comment) or mentions_name(text, snap.me_name)
        )
        task = snap.tasks.get(task_id)
        name = str(task.get("name")) if task and task.get("name") else task_id
        return _Draft(
            id=comment_id,
            ts_ms=ts_ms,
            group=("task", task_id),
            thread_id=task_id,
            container=Container("task", task_id, name),
            author=author,
            text=text,
            permalink=f"https://app.clickup.com/t/{task_id}?comment={comment_id}",
            is_mention=is_mention,
            is_dm=False,
            task_id=task_id,
        )

    seen: set[str] = set()
    for task_dir in task_dirs:
        task_id = task_dir.name
        for page in _pages(task_dir):
            for comment in page.get("comments") or []:
                draft = build(comment, task_id)
                if draft and draft.id not in seen:
                    seen.add(draft.id)
                    comment_task[draft.id] = task_id
                    drafts.append(draft)

    replies_dir = snap.dir / "replies"
    if replies_dir.is_dir():
        for path in sorted(replies_dir.glob("*.json")):
            parent_id = path.stem
            task_id = comment_task.get(parent_id)
            if task_id is None:
                warnings.append(
                    f"replies/{path.name}: parent comment {parent_id} is not in this "
                    "snapshot, so its task is unknown; replies skipped"
                )
                continue
            for reply in _load(path).get("comments") or []:
                draft = build(reply, task_id)
                if draft and draft.id not in seen:
                    seen.add(draft.id)
                    drafts.append(draft)
    return drafts


def _chat_drafts(snap: _Snapshot, warnings: list[str]) -> list[_Draft]:
    raw: list[tuple[dict[str, Any], str]] = []  # (message, channel_id)
    messages_root = snap.dir / "chat" / "messages"
    if messages_root.is_dir():
        for channel_dir in sorted(p for p in messages_root.iterdir() if p.is_dir()):
            for page in _pages(channel_dir):
                for message in page.get("data") or []:
                    raw.append((message, str(message.get("parent_channel") or channel_dir.name)))
    replies_root = snap.dir / "chat" / "replies"
    if replies_root.is_dir():
        for parent_dir in sorted(p for p in replies_root.iterdir() if p.is_dir()):
            for page in _pages(parent_dir):
                for message in page.get("data") or []:
                    message.setdefault("parent_message", parent_dir.name)
                    raw.append((message, str(message.get("parent_channel") or "")))

    has_replies = {str(m["parent_message"]) for m, _ in raw if m.get("parent_message")}
    authors_by_channel: dict[str, set[str]] = defaultdict(set)
    for message, channel_id in raw:
        authors_by_channel[channel_id].add(str(message.get("user_id", "")))

    drafts: list[_Draft] = []
    seen: set[str] = set()
    for message, channel_id in raw:
        message_id = str(message.get("id", ""))
        ts_ms = parse_ms(message.get("date"))
        if not message_id or ts_ms is None or not channel_id:
            warnings.append(f"chat: skipped a message with no id, date or channel ({message_id})")
            continue
        if message_id in seen:
            continue
        seen.add(message_id)
        channel = snap.channels.get(channel_id) or {}
        channel_type = str(channel.get("type") or "")
        is_dm = channel_type in _DM_TYPES
        name = str(channel.get("name") or "")
        if not name and is_dm:
            others = sorted(
                snap.user_name(uid)
                for uid in authors_by_channel[channel_id]
                if uid and uid != snap.me_id
            )
            name = ", ".join(others)
        author_id = str(message.get("user_id") or UNKNOWN_USER)
        text = str(message.get("content") or "")
        mine = author_id == snap.me_id
        is_mention = not mine and (
            snap.me_id in snap.tagged.get(message_id, set()) or mentions_name(text, snap.me_name)
        )
        parent = message.get("parent_message")
        if parent:
            thread_id: str | None = str(parent)
        elif message_id in has_replies or float(message.get("replies_count") or 0) > 0:
            thread_id = message_id
        else:
            thread_id = None
        group = ("chat-thread", thread_id) if thread_id else ("chat-channel", channel_id)
        workspace = str(channel.get("workspace_id") or snap.manifest.get("team_id") or "")
        # The API returns no web link for chat; this opens the channel, not the
        # message (an unverified URL shape -- ARCHITECTURE.md, known limits).
        permalink = (
            f"https://app.clickup.com/{workspace}/chat/r/{channel_id}"
            if workspace
            else "https://app.clickup.com/"
        )
        drafts.append(
            _Draft(
                id=message_id,
                ts_ms=ts_ms,
                group=group,
                thread_id=thread_id,
                container=Container(
                    _CHANNEL_KIND.get(channel_type, "chat"), channel_id, name or channel_id
                ),
                author=Author(author_id, snap.user_name(author_id)),
                text=text,
                permalink=permalink,
                is_mention=is_mention,
                is_dm=is_dm,
            )
        )
    return drafts


def _concerns_me(draft: _Draft, snap: _Snapshot, mention_groups: set[tuple[str, str]]) -> bool:
    if draft.is_dm or draft.is_mention or draft.group in mention_groups:
        return True
    if draft.task_id is not None:
        task = snap.tasks.get(draft.task_id) or {}
        people = [*(task.get("assignees") or []), *(task.get("watchers") or [])]
        if task.get("creator"):
            people.append(task["creator"])
        return any(str(p.get("id")) == snap.me_id for p in people if isinstance(p, dict))
    return False


def normalize_raw_dir(raw_dir: Path) -> NormalizeResult:
    snap = _Snapshot(raw_dir)
    warnings: list[str] = []
    drafts = _task_drafts(snap, warnings) + _chat_drafts(snap, warnings)

    latest: dict[tuple[str, str], tuple[int, bool]] = {}
    for d in drafts:
        # On a timestamp tie, count the thread as answered by me.
        key = (d.ts_ms, d.author.id == snap.me_id)
        if d.group not in latest or key > latest[d.group]:
            latest[d.group] = key
    # Chat top-level streams are "threads" only for awaiting_reply; a mention
    # in a channel's top-level stream does not pull the whole channel in.
    mention_groups = {d.group for d in drafts if d.is_mention and d.group[0] != "chat-channel"}

    is_inbox = snap.manifest.get("kind") == "inbox"
    since_ms = parse_ms(snap.manifest.get("since_ms")) if is_inbox else None

    messages: list[Message] = []
    for d in sorted(drafts, key=lambda d: (d.ts_ms, d.id)):
        if is_inbox:
            if since_ms is not None and d.ts_ms < since_ms:
                continue
            if not _concerns_me(d, snap, mention_groups):
                continue
        mine = d.author.id == snap.me_id
        last_is_mine = latest[d.group][1]
        messages.append(
            Message(
                id=d.id,
                thread_id=d.thread_id,
                container=d.container,
                author=d.author,
                ts=iso_from_ms(d.ts_ms),
                text=d.text,
                permalink=d.permalink,
                is_mention=d.is_mention,
                is_dm=d.is_dm,
                awaiting_reply=(not mine) and (d.is_mention or d.is_dm) and not last_is_mine,
            )
        )
    warnings += [str(n) for n in snap.manifest.get("notes") or []]
    return NormalizeResult(messages=messages, warnings=warnings, manifest=snap.manifest)
