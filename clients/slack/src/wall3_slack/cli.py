"""``slack`` CLI. Human-readable by default, JSONL (one contract Message per line) with --json.

Exit codes: 0 ok, 1 config/API error, 3 write refused because --confirm was absent,
4 --confirm given without a matching --expect-sha256 (the approved-text hash).
(argparse usage errors exit 2, as argparse always does.)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .approval import (
    EXIT_HASH_MISMATCH,
    FlagOnce,
    StoreOnce,
    TextFileError,
    hash_problem,
    read_text_bytes,
    sha256_hex,
)
from .client import SlackAPIError, SlackClient
from .config import ConfigError, SlackConfig, raw_cache_root
from .extract import (
    NotFoundError,
    extract_channel,
    extract_inbox,
    extract_search,
    extract_thread,
    lookup_user,
    new_run_dir,
)
from .models import Message, Whoami
from .normalize import NormalizeError, normalize_dir
from .normalize.fields import user_display_name

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 3
EXIT_HASH = EXIT_HASH_MISMATCH

_SINCE = re.compile(r"^(\d+)([mhd])$")
_PERMALINK = re.compile(r"/archives/([A-Z0-9]+)/p(\d{7,})")
_CHANNEL_TS = re.compile(r"^([A-Z0-9]+):(\d+\.\d+)$")


# ------------------------------------------------------------ seams for tests


def make_client() -> SlackClient:
    config = SlackConfig.from_env()
    return SlackClient(config.token, max_retries=config.max_retries, timeout=config.timeout_seconds)


def raw_root() -> Path:
    return raw_cache_root()


def utc_now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------- parsing


def parse_since(value: str) -> timedelta:
    match = _SINCE.match(value.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"expected e.g. 30m, 4h, 24h, 7d; got {value!r}")
    amount, unit = int(match.group(1)), match.group(2)
    if amount <= 0:
        raise argparse.ArgumentTypeError("--since must be positive")
    return {"m": timedelta(minutes=amount), "h": timedelta(hours=amount)}.get(
        unit, timedelta(days=amount)
    )


def parse_thread_ref(value: str) -> tuple[str, str]:
    """A Slack permalink URL or ``C...:ts`` -> (channel, thread ts)."""
    value = value.strip()
    direct = _CHANNEL_TS.match(value)
    if direct:
        return direct.group(1), direct.group(2)
    link = _PERMALINK.search(value)
    if link:
        channel, digits = link.group(1), link.group(2)
        thread = re.search(r"[?&]thread_ts=(\d+\.\d+)", value)
        ts = thread.group(1) if thread else f"{digits[:-6]}.{digits[-6:]}"
        return channel, ts
    raise ValueError(f"not a Slack permalink or CHANNEL:ts reference: {value!r}")


# ------------------------------------------------------------------- output


def _print_messages(messages: list[Message], *, as_json: bool) -> None:
    for msg in messages:
        if as_json:
            print(json.dumps(msg.to_dict(), ensure_ascii=False))
            continue
        flags = [
            f
            for f, on in (("mention", msg.is_mention), ("AWAITING REPLY", msg.awaiting_reply))
            if on
        ]
        flag_text = f"  [{', '.join(flags)}]" if flags else ""
        thread = "  (thread)" if msg.thread_id and msg.id != msg.thread_id else ""
        where = f"{msg.container.kind} {msg.container.name}"
        print(f"{msg.ts}  {where}  {msg.author.name}{thread}{flag_text}")
        for line in msg.text.splitlines() or [""]:
            print(f"    {line}")
        print(f"    {msg.permalink}")


def _report_run(raw_dir: Path, *, fetched: bool) -> None:
    """Truncation and skips go to stderr, so --json stdout stays pure JSONL."""
    manifest_path = raw_dir / "manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for note in manifest.get("truncated") or []:
        print(f"slack: truncated: {note}", file=sys.stderr)
    for note in manifest.get("skipped") or []:
        print(f"slack: skipped: {note}", file=sys.stderr)
    if fetched:
        print(f"slack: raw responses in {raw_dir}", file=sys.stderr)


def _emit_from(
    raw_dir: Path, *, as_json: bool, keep_last: int | None = None, fetched: bool = True
) -> int:
    messages = normalize_dir(raw_dir)
    if keep_last is not None:
        messages = messages[-keep_last:] if keep_last > 0 else []
    _print_messages(messages, as_json=as_json)
    _report_run(raw_dir, fetched=fetched)
    return EXIT_OK


# ------------------------------------------------------------------- verbs


def cmd_whoami(args: argparse.Namespace) -> int:
    client = make_client()
    auth = client.auth_test()
    user_id = str(auth["user_id"])
    try:
        name = user_display_name(client.users_info(user_id).get("user") or {})
    except SlackAPIError:
        name = ""
    me = Whoami(id=user_id, name=name or str(auth.get("user") or user_id))
    if args.json:
        print(json.dumps(me.to_dict(), ensure_ascii=False))
    else:
        print(f"{me.name} ({me.id}) on {auth.get('team', '?')} -- {auth.get('url', '')}")
    return EXIT_OK


def cmd_inbox(args: argparse.Namespace) -> int:
    now = utc_now()
    out = new_run_dir(raw_root(), now)
    extract_inbox(
        make_client(), out, oldest=now - args.since, now=now, since_label=args.since_label
    )
    return _emit_from(out, as_json=args.json)


def cmd_thread(args: argparse.Namespace) -> int:
    try:
        channel, ts = parse_thread_ref(args.ref)
    except ValueError as exc:
        print(f"slack: {exc}", file=sys.stderr)
        return EXIT_ERROR
    now = utc_now()
    out = new_run_dir(raw_root(), now)
    extract_thread(make_client(), out, channel=channel, ts=ts, now=now)
    return _emit_from(out, as_json=args.json)


def cmd_channel(args: argparse.Namespace) -> int:
    now = utc_now()
    out = new_run_dir(raw_root(), now)
    oldest = now - args.since if args.since else None
    extract_channel(make_client(), out, ref=args.ref, oldest=oldest, limit=args.limit, now=now)
    return _emit_from(out, as_json=args.json, keep_last=args.limit)


def cmd_search(args: argparse.Namespace) -> int:
    now = utc_now()
    out = new_run_dir(raw_root(), now)
    extract_search(make_client(), out, query=args.query, count=50, now=now)
    return _emit_from(out, as_json=args.json)


def cmd_user(args: argparse.Namespace) -> int:
    user = lookup_user(make_client(), args.ref)
    profile: dict[str, Any] = user.get("profile") or {}
    rows = [
        ("id", user.get("id")),
        ("name", user_display_name(user)),
        ("handle", user.get("name")),
        ("real name", profile.get("real_name") or user.get("real_name")),
        ("title", profile.get("title")),
        ("timezone", user.get("tz")),
        ("bot", "yes" if user.get("is_bot") else "no"),
        ("deactivated", "yes" if user.get("deleted") else "no"),
    ]
    for label, value in rows:
        if value:
            print(f"{label:>12}: {value}")
    return EXIT_OK


def cmd_normalize(args: argparse.Namespace) -> int:
    return _emit_from(Path(args.raw_dir), as_json=args.json, fetched=False)


def cmd_send(args: argparse.Namespace) -> int:
    # Read once. The bytes read here are the bytes hashed and the bytes posted.
    try:
        data = read_text_bytes(args.text_file)
    except TextFileError as exc:
        print(f"slack: --text-file: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        print(f"slack: --text-file is not UTF-8: {args.text_file}", file=sys.stderr)
        return EXIT_ERROR
    if not text.strip():
        print(f"slack: --text-file is empty: {args.text_file}", file=sys.stderr)
        return EXIT_ERROR
    digest = sha256_hex(data)

    if not args.confirm:
        # Preview only. No client is constructed, so no network call of any kind.
        print("DRY RUN -- nothing was sent.")
        print(f"channel:   {args.channel}")
        print(f"thread_ts: {args.thread_ts or '(none: top-level message)'}")
        print("----- text -----")
        print(text, end="" if text.endswith("\n") else "\n")
        print("----- end -----")
        print(f"sha256: {digest}")
        if args.expect_sha256 is not None:
            verdict = hash_problem(args.expect_sha256, digest)
            print("--expect-sha256: matches" if verdict is None else f"--expect-sha256: {verdict}")
        print(
            "To post exactly this text once Don approves it, re-run the same command with "
            f"--confirm --expect-sha256 {digest}"
        )
        return EXIT_REFUSED

    problem = hash_problem(args.expect_sha256, digest)
    if problem is not None:
        print(f"slack: {problem}", file=sys.stderr)
        return EXIT_HASH

    client = make_client()
    posted = client.chat_post_message(args.channel, text, thread_ts=args.thread_ts)
    channel, ts = str(posted.get("channel") or args.channel), str(posted.get("ts") or "")
    try:
        permalink = client.chat_get_permalink(channel, ts).get("permalink")
    except SlackAPIError as exc:
        # The post landed. Failing here would invite a re-send and a duplicate.
        print(f"posted to {channel} at ts {ts} (permalink lookup failed: {exc.error})")
        return EXIT_OK
    print(permalink)
    return EXIT_OK


# -------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    # allow_abbrev=False everywhere: otherwise `--conf` silently means `--confirm`
    # and `--text` means `--text-file`. A send must be asked for in full.
    # Every option is StoreOnce/FlagOnce: argparse keeps the *last* of a repeated
    # option, so `--channel A --channel B` would preview one target and post to another.
    parser = argparse.ArgumentParser(prog="slack", description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("whoami", allow_abbrev=False, help="who the token acts as")
    p.add_argument("--json", action=FlagOnce)
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser(
        "inbox", allow_abbrev=False, help="DMs, mentions and my threads since a window"
    )
    p.add_argument(
        "--since", action=StoreOnce, default="24h", help="30m, 4h, 24h, 7d (default 24h)"
    )
    p.add_argument("--json", action=FlagOnce)
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser(
        "thread", allow_abbrev=False, help="a whole thread, by permalink or CHANNEL:ts"
    )
    p.add_argument("ref")
    p.add_argument("--json", action=FlagOnce)
    p.set_defaults(func=cmd_thread)

    p = sub.add_parser(
        "channel", allow_abbrev=False, help="recent messages in a channel, by name or id"
    )
    p.add_argument("ref")
    p.add_argument(
        "--since", action=StoreOnce, default=None, help="30m, 4h, 24h, 7d (default: no window)"
    )
    p.add_argument("--limit", action=StoreOnce, type=int, default=50)
    p.add_argument("--json", action=FlagOnce)
    p.set_defaults(func=cmd_channel)

    p = sub.add_parser("search", allow_abbrev=False, help="search.messages with Slack query syntax")
    p.add_argument("query")
    p.add_argument("--json", action=FlagOnce)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("user", allow_abbrev=False, help="look up a user by id or @name")
    p.add_argument("ref")
    p.set_defaults(func=cmd_user)

    p = sub.add_parser("normalize", allow_abbrev=False, help="raw dir -> messages, offline")
    p.add_argument("raw_dir")
    p.add_argument("--json", action=FlagOnce)
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser(
        "send",
        allow_abbrev=False,
        help="post a message as you (requires --confirm --expect-sha256)",
    )
    p.add_argument("--channel", action=StoreOnce, required=True)
    p.add_argument("--thread-ts", action=StoreOnce, default=None)
    p.add_argument("--text-file", action=StoreOnce, required=True)
    p.add_argument("--expect-sha256", action=StoreOnce, default=None, help="from the dry run")
    p.add_argument("--confirm", action=FlagOnce)
    p.set_defaults(func=cmd_send)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "since", None) is not None:
        try:
            delta = parse_since(args.since)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
        args.since_label = args.since
        args.since = delta
    try:
        return int(args.func(args))
    except (ConfigError, NormalizeError, NotFoundError) as exc:
        print(f"slack: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except SlackAPIError as exc:
        print(f"slack: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
