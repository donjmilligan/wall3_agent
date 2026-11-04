# wall3_clickup

ClickUp REST client and `clickup` CLI for the wall3 agent harness. Claude uses it
to read ClickUp (task comments, assigned tasks, Chat) and to *draft* replies.
Nothing is posted unless Don approves it and the command carries `--confirm`.

It calls the REST API directly with a personal token rather than going through
the ClickUp MCP connector, which is rate-limited much harder.

## Setup

```bash
cd clients/clickup
uv sync
```

1. **Token.** ClickUp → avatar → **Settings → Apps → API Token** → generate
   (it starts with `pk_`). Add `CLICKUP_API_TOKEN=pk_...` to the repo-root
   `.env` (gitignored). See `.env.example`.
2. **Workspace id.** Run `bin/clickup whoami`. If `CLICKUP_TEAM_ID` is unset it
   lists every workspace the token can see, with the exact line to add to `.env`.

`.env` lookup works from a git worktree. First hit wins:

1. a variable already exported in the shell
2. `clients/clickup/.env` (per-tool override)
3. `<working repo root>/.env` (the worktree's own, if you made one)
4. `<primary checkout>/.env` (the shared file, and the normal case; found with
   `git rev-parse --path-format=absolute --git-common-dir`)

If the token is missing, the error lists every path it searched and marks each
one found or not found. Read that list before you mint a new token.

## Commands

From the repo root, use `bin/clickup ...` (the same as
`uv run --project clients/clickup clickup ...`). Every read verb accepts
`--json`, which prints JSON Lines.

| Verb | What it does |
|---|---|
| `whoami` | The token's user. `--json` → `{"source":"clickup","id":...,"name":...}` |
| `inbox [--since 24h]` | Recent task comments and chat messages that mention me, DM me, or sit on tasks I'm assigned to, watch, or created |
| `task <id> [--comments]` | One task. With `--comments`, its whole comment history too |
| `search <query>` | Matches tasks client-side (see Known limits) |
| `mine [--status S]` | Tasks assigned to me |
| `chat channels` | Chat channels, DMs and group DMs |
| `chat messages <channel_id>` | The latest ~100 messages in a channel, with thread replies |
| `normalize <raw_dir>` | Offline: turns a raw snapshot into Messages (no token needed) |
| `comment <id> --text-file F [--confirm]` | Posts a task comment |
| `status <id> <status> --comment-file F [--confirm]` | Posts the comment, then changes the status |

```bash
bin/clickup whoami --json
bin/clickup inbox --since 4h --json          # 30m, 4h, 24h, 7d
bin/clickup task abc123 --comments --json    # {"task": {...}, "comments": [Message, ...]}
bin/clickup search "login redirect"
bin/clickup mine --status "in progress" --json
bin/clickup chat channels
bin/clickup chat messages <channel_id> --json
bin/clickup normalize clients/clickup/tests/fixtures/raw/inbox --json

bin/clickup comment abc123 --text-file drafts/reply.txt             # preview, exit 3
bin/clickup comment abc123 --text-file drafts/reply.txt --confirm   # posts
bin/clickup status abc123 "in review" --comment-file drafts/why.txt --confirm
```

`inbox`, `task`, `chat messages` and `normalize` print **Messages** in the format
shared with the Slack client:

```json
{"source": "clickup", "id": "5201", "thread_id": "ghi789",
 "container": {"kind": "task", "id": "ghi789", "name": "Update onboarding docs"},
 "author": {"id": "1002", "name": "Alice Example"}, "ts": "2026-11-04T12:15:00Z",
 "text": "@Don Example the onboarding checklist needs your sign-off.",
 "permalink": "https://app.clickup.com/t/ghi789?comment=5201",
 "is_mention": true, "is_dm": false, "awaiting_reply": true}
```

`mine`, `search` and `task` (without `--comments`) print task summaries:
`{"id", "name", "status", "url", "list", "assignees", "date_updated"}`.

Exit codes: `0` ok, `1` config/API error, `2` usage error, `3` write refused
because `--confirm` was not given.

Network verbs save the raw API responses to `.cache/raw/clickup/<UTC timestamp>/`
(gitignored) and print that path to stderr. Re-run `normalize` on the path to
reproduce the output without calling ClickUp again.

## Safety

- **Writes need `--confirm`.** Without it, `comment` and `status` print exactly
  what would be sent (target task, full text, new status) and exit 3. They make
  **no network call at all**, so the preview works even without a token.
- **Text comes only from a file** (`--text-file`, `--comment-file`). It is never
  interpolated from a shell string, so `$(...)`, backticks and quotes reach
  ClickUp literally. Trailing whitespace is trimmed. An empty file is refused.
- **No silent status flips.** `status` always posts its comment first. The client
  refuses a status change on any task it has not commented on
  (`SilentStatusChange`).
- **Writes are never retried after a server error.** A POST that got a 5xx may
  have succeeded anyway, so retrying could double-post to a real person. 429s
  are retried, because a rate-limited request was rejected.
- **The token is never printed or written.** It is hidden from `repr()` and
  appears in no error message. Raw snapshots contain API responses only.
- In the harness, an outbound-guard hook also asks Don before any
  `bin/clickup comment|status` runs.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

See `ARCHITECTURE.md` for design decisions, the verified endpoint list and the
known limits.
