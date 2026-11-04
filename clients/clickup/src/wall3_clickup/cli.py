"""`clickup` -- read ClickUp for the harness; write only with --confirm.

Exit codes: 0 ok, 1 config/API error, 2 usage error (argparse),
3 write refused because --confirm was not given.

Write verbs (`comment`, `status`) take their text only from a file, never
from a shell-interpolated string, and without `--confirm` they print exactly
what would be sent and exit 3 having made no network call at all.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from collections.abc import Callable, Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any

from .client import (
    ClickUpAPIError,
    ClickUpAuthError,
    ClickUpClient,
    HttpSession,
    SilentStatusChange,
)
from .config import ClickUpConfig, ConfigError, raw_cache_root
from .extract import RawWriter, extract_channel, extract_inbox, extract_task
from .extract.raw_writer import safe_component
from .models import Message, Whoami
from .normalize import (
    NormalizeError,
    NormalizeResult,
    normalize_raw_dir,
    task_matches,
    task_summary,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 3

LIST_MAX_PAGES = 5  # mine / search: up to 500 tasks
CHANNEL_LIST_MAX_PAGES = 5

_SINCE = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_since(value: str) -> timedelta:
    """`30m`, `4h`, `24h`, `7d` -> timedelta. argparse turns ValueError into exit 2."""
    match = _SINCE.match(value.strip())
    if not match or int(match.group(1)) == 0:
        raise argparse.ArgumentTypeError(
            f"invalid --since {value!r}: use a positive number + m/h/d, e.g. 30m, 4h, 24h, 7d"
        )
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


class _Context:
    """Lazily built config + client, so offline verbs never need a token."""

    def __init__(
        self,
        *,
        session: HttpSession | None,
        sleep: Callable[[float], None] | None,
        raw_root: Path | None,
        now: Callable[[], float],
    ) -> None:
        self._session = session
        self._sleep = sleep
        self.raw_root = raw_root or raw_cache_root()
        self.now = now
        self._config: ClickUpConfig | None = None
        self._client: ClickUpClient | None = None

    @property
    def config(self) -> ClickUpConfig:
        if self._config is None:
            self._config = ClickUpConfig.from_env(require_token=True)
        return self._config

    @property
    def client(self) -> ClickUpClient:
        if self._client is None:
            kwargs: dict[str, Any] = {"session": self._session}
            if self._sleep is not None:
                kwargs["sleep"] = self._sleep
            self._client = ClickUpClient(self.config, **kwargs)
        return self._client

    def team_id(self) -> str:
        if self.config.team_id:
            return self.config.team_id
        raise ConfigError("CLICKUP_TEAM_ID is not set.\n" + _teams_hint(self.client.get_teams()))


def _teams_hint(teams_response: dict[str, Any]) -> str:
    teams = teams_response.get("teams") or []
    if not teams:
        return "Your token can see no workspaces."
    lines = ["Set CLICKUP_TEAM_ID (in .env or the environment) to one of:"]
    lines += [f"  CLICKUP_TEAM_ID={t.get('id')}   # {t.get('name', '')}" for t in teams]
    return "\n".join(lines)


# ---------------------------------------------------------------- output helpers


def _emit_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _one_line(text: str, limit: int = 200) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _print_messages(messages: Iterable[Message], as_json: bool) -> None:
    count = 0
    for m in messages:
        count += 1
        if as_json:
            _emit_json(m.to_dict())
            continue
        flags = [
            name
            for name, on in (
                ("MENTION", m.is_mention),
                ("DM", m.is_dm),
                ("AWAITING", m.awaiting_reply),
            )
            if on
        ]
        where = f"[{m.container.kind}] {m.container.name}"
        print(f"{m.ts}  {where}  {m.author.name}  {' '.join(flags)}".rstrip())
        print(f"    {_one_line(m.text)}")
        print(f"    {m.permalink}")
    if not as_json and count == 0:
        print("(no messages)")


def _print_warnings(result: NormalizeResult) -> None:
    for warning in result.warnings:
        print(f"note: {warning}", file=sys.stderr)


def _print_tasks(tasks: list[dict[str, Any]], as_json: bool) -> None:
    for t in tasks:
        if as_json:
            _emit_json(t)
        else:
            who = ", ".join(t["assignees"]) or "unassigned"
            print(f"{t['id']}  [{t['status']}] {t['name']}  ({who})  {t['url']}")
    if not as_json and not tasks:
        print("(no tasks)")


def _read_text_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").rstrip()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not text:
        raise ConfigError(f"{path} is empty; refusing to post an empty comment")
    return text


# ---------------------------------------------------------------- verbs


def cmd_whoami(args: argparse.Namespace, ctx: _Context) -> int:
    user = ctx.client.get_user().get("user") or {}
    me = Whoami(id=str(user.get("id", "")), name=str(user.get("username") or ""))
    if args.json:
        _emit_json(me.to_dict())
    else:
        print(f"{me.name} (id {me.id})")
    teams = ctx.client.get_teams()
    known = {str(t.get("id")) for t in teams.get("teams") or []}
    if not ctx.config.team_id:
        print("CLICKUP_TEAM_ID is not set.\n" + _teams_hint(teams), file=sys.stderr)
    elif ctx.config.team_id not in known:
        print(
            f"warning: CLICKUP_TEAM_ID={ctx.config.team_id} is not a workspace this token "
            "can see.\n" + _teams_hint(teams),
            file=sys.stderr,
        )
    elif not args.json:
        print(f"workspace: {ctx.config.team_id}")
    return EXIT_OK


def _finish_snapshot(raw_dir: Path) -> NormalizeResult:
    print(f"raw snapshot: {raw_dir}", file=sys.stderr)
    result = normalize_raw_dir(raw_dir)
    _print_warnings(result)
    return result


def cmd_inbox(args: argparse.Namespace, ctx: _Context) -> int:
    team_id = ctx.team_id()
    since_ms = int((ctx.now() - args.since.total_seconds()) * 1000)
    writer = RawWriter.create(ctx.raw_root)
    raw_dir = extract_inbox(ctx.client, writer, team_id=team_id, since_ms=since_ms)
    result = _finish_snapshot(raw_dir)
    _print_messages(result.messages, args.json)
    return EXIT_OK


def cmd_task(args: argparse.Namespace, ctx: _Context) -> int:
    task_id = safe_component(args.task_id)
    writer = RawWriter.create(ctx.raw_root)
    raw_dir = extract_task(
        ctx.client,
        writer,
        task_id=task_id,
        team_id=ctx.config.team_id,
        with_comments=args.comments,
    )
    print(f"raw snapshot: {raw_dir}", file=sys.stderr)
    with (raw_dir / "tasks" / f"{task_id}.json").open(encoding="utf-8") as fh:
        summary = task_summary(json.load(fh))
    messages: list[Message] = []
    if args.comments:
        result = normalize_raw_dir(raw_dir)
        _print_warnings(result)
        messages = result.messages
    if args.json:
        if args.comments:
            _emit_json({"task": summary, "comments": [m.to_dict() for m in messages]})
        else:
            _emit_json(summary)
    else:
        _print_tasks([summary], as_json=False)
        if args.comments:
            print()
            _print_messages(messages, as_json=False)
    return EXIT_OK


def _collect_tasks(pages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for page in pages:
        tasks.extend(page.get("tasks") or [])
    return tasks


def cmd_search(args: argparse.Namespace, ctx: _Context) -> int:
    team_id = ctx.team_id()
    tasks = _collect_tasks(
        ctx.client.iter_team_task_pages(
            team_id, max_pages=LIST_MAX_PAGES, include_closed=True, subtasks=True
        )
    )
    print(
        f"note: ClickUp's API has no task search; matched client-side against "
        f"{len(tasks)} tasks fetched with order_by=updated (at most {LIST_MAX_PAGES * 100})",
        file=sys.stderr,
    )
    _print_tasks([task_summary(t) for t in tasks if task_matches(t, args.query)], args.json)
    return EXIT_OK


def cmd_mine(args: argparse.Namespace, ctx: _Context) -> int:
    team_id = ctx.team_id()
    me_id = str((ctx.client.get_user().get("user") or {}).get("id", ""))
    statuses = [args.status] if args.status else None
    tasks = _collect_tasks(
        ctx.client.iter_team_task_pages(
            team_id,
            max_pages=LIST_MAX_PAGES,
            assignees=[me_id],
            statuses=statuses,
            # A closed status (e.g. "complete") only matches with include_closed.
            include_closed=bool(statuses),
            subtasks=True,
        )
    )
    _print_tasks([task_summary(t) for t in tasks], args.json)
    return EXIT_OK


def cmd_chat_channels(args: argparse.Namespace, ctx: _Context) -> int:
    team_id = ctx.team_id()
    for page in ctx.client.iter_chat_channel_pages(team_id, max_pages=CHANNEL_LIST_MAX_PAGES):
        for channel in page.get("data") or []:
            row = {
                "id": str(channel.get("id", "")),
                "name": str(channel.get("name") or ""),
                "type": str(channel.get("type") or ""),
            }
            if args.json:
                _emit_json(row)
            else:
                print(f"{row['id']}  [{row['type']}] {row['name']}")
    return EXIT_OK


def cmd_chat_messages(args: argparse.Namespace, ctx: _Context) -> int:
    channel_id = safe_component(args.channel_id)
    team_id = ctx.team_id()
    writer = RawWriter.create(ctx.raw_root)
    raw_dir = extract_channel(ctx.client, writer, team_id=team_id, channel_id=channel_id)
    result = _finish_snapshot(raw_dir)
    _print_messages(result.messages, args.json)
    return EXIT_OK


def cmd_normalize(args: argparse.Namespace, ctx: _Context) -> int:
    result = normalize_raw_dir(Path(args.raw_dir))
    _print_warnings(result)
    _print_messages(result.messages, args.json)
    return EXIT_OK


def _preview(actions: list[dict[str, str]], as_json: bool) -> int:
    if as_json:
        _emit_json({"dry_run": True, "actions": actions})
    else:
        print("DRY RUN -- nothing was sent. Re-run with --confirm to perform:")
        for number, action in enumerate(actions, start=1):
            print(f"\n[{number}] {action['method']} {action['description']}")
            if "text" in action:
                print(f"----- text ({len(action['text'])} chars) -----")
                print(action["text"])
                print("----- end -----")
    return EXIT_REFUSED


def _comment_action(task_id: str, text: str) -> dict[str, str]:
    return {
        "method": "POST",
        "description": f"comment on task {task_id} (https://app.clickup.com/t/{task_id})",
        "target": task_id,
        "text": text,
    }


def cmd_comment(args: argparse.Namespace, ctx: _Context) -> int:
    task_id = safe_component(args.task_id)
    text = _read_text_file(Path(args.text_file))
    if not args.confirm:
        return _preview([_comment_action(task_id, text)], args.json)
    created = ctx.client.add_comment(task_id, text)
    comment_id = str(created.get("id", ""))
    out = {
        "task_id": task_id,
        "comment_id": comment_id,
        "permalink": f"https://app.clickup.com/t/{task_id}?comment={comment_id}",
    }
    if args.json:
        _emit_json(out)
    else:
        print(f"posted comment {comment_id}: {out['permalink']}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace, ctx: _Context) -> int:
    task_id = safe_component(args.task_id)
    text = _read_text_file(Path(args.comment_file))
    if not args.confirm:
        return _preview(
            [
                _comment_action(task_id, text),
                {
                    "method": "PUT",
                    "description": f"status of task {task_id} -> {args.status!r}",
                    "target": task_id,
                    "status": args.status,
                },
            ],
            args.json,
        )
    created = ctx.client.add_comment(task_id, text)
    comment_id = str(created.get("id", ""))
    if not args.json:
        print(f"posted comment {comment_id} on {task_id}")
    try:
        ctx.client.update_task_status(task_id, args.status)
    except ClickUpAPIError:
        print(
            f"error: the comment ({comment_id}) was posted but the status change failed; "
            f"task {task_id} is still in its old status",
            file=sys.stderr,
        )
        raise
    if args.json:
        _emit_json({"task_id": task_id, "comment_id": comment_id, "status": args.status})
    else:
        print(f"set {task_id} status -> {args.status!r}")
    return EXIT_OK


# ---------------------------------------------------------------- parser / main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clickup", description="Read ClickUp; write only with --confirm."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: Callable[..., int], help_text: str, *, parent: Any = sub) -> Any:
        p = parent.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        p.add_argument("--json", action="store_true", help="JSONL output")
        return p

    add("whoami", cmd_whoami, "the token's user; lists workspaces if CLICKUP_TEAM_ID is unset")
    p = add("inbox", cmd_inbox, "recent comments/chat that mention, DM or concern me")
    p.add_argument("--since", type=parse_since, default=parse_since("24h"), help="30m, 4h, 24h, 7d")
    p = add("task", cmd_task, "one task (and its comments with --comments)")
    p.add_argument("task_id")
    p.add_argument("--comments", action="store_true")
    p = add("search", cmd_search, "client-side match over recently updated tasks")
    p.add_argument("query")
    p = add("mine", cmd_mine, "tasks assigned to me")
    p.add_argument("--status", default=None)

    chat = sub.add_parser("chat", help="v3 Chat (experimental API)")
    chat_sub = chat.add_subparsers(dest="chat_command", required=True)
    add("channels", cmd_chat_channels, "list chat channels", parent=chat_sub)
    p = add("messages", cmd_chat_messages, "latest messages in one channel", parent=chat_sub)
    p.add_argument("channel_id")

    p = add("normalize", cmd_normalize, "offline: raw snapshot dir -> Messages")
    p.add_argument("raw_dir")

    p = add("comment", cmd_comment, "post a comment (preview unless --confirm)")
    p.add_argument("task_id")
    p.add_argument("--text-file", required=True)
    p.add_argument("--confirm", action="store_true")

    p = add("status", cmd_status, "comment, then change status (preview unless --confirm)")
    p.add_argument("task_id")
    p.add_argument("status")
    p.add_argument("--comment-file", required=True)
    p.add_argument("--confirm", action="store_true")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    session: HttpSession | None = None,
    sleep: Callable[[float], None] | None = None,
    raw_root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> int:
    """Entry point. The keyword-only arguments are test seams."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    ctx = _Context(session=session, sleep=sleep, raw_root=raw_root, now=now)
    try:
        return args.func(args, ctx)
    except ClickUpAuthError as exc:
        print(
            f"error: ClickUp rejected the token (HTTP {exc.status}) for {exc.method} {exc.url}. "
            "Check CLICKUP_API_TOKEN.",
            file=sys.stderr,
        )
    except (ConfigError, NormalizeError, ClickUpAPIError, SilentStatusChange, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
