# wall3_slack

A Slack Web API client and CLI that reads your DMs, @-mentions and threads, and
posts replies **only when you pass `--confirm` together with the `--expect-sha256`
digest of the exact text Don approved**. It uses a *user* token
(`xoxp-...`), so it acts as you: it can read what you can read, and anything it
sends is sent as you.

From the repo root, use the wrapper: `bin/slack <verb> ...`. It is the same as
`uv run --project clients/slack slack <verb> ...`.

Design decisions, verified API facts, and the inbox strategy are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Setup (once)

1. **Create the app from the manifest.** Go to <https://api.slack.com/apps>, then
   **Create New App**, **From a manifest**. Pick your workspace and paste the
   contents of [`slack-app-manifest.yaml`](slack-app-manifest.yaml). It asks
   for user-token scopes only and has no bot user:

   | Scope | Used for |
   |---|---|
   | `channels:history`, `groups:history`, `im:history`, `mpim:history` | reading messages and threads (`conversations.history` / `.replies`) |
   | `channels:read`, `groups:read`, `im:read`, `mpim:read` | listing your DMs, resolving channel names (`conversations.list` / `.info`) |
   | `users:read` | showing names instead of ids (`users.info`, `users.list`) |
   | `search:read` | finding @-mentions and your recent threads (`search.messages`) |
   | `chat:write` | `send --confirm` only (`chat.postMessage`) |

2. **Install it.** On the app's **OAuth & Permissions** page, click
   **Install to Workspace** and approve. Do not enable distribution: the app
   stays an internal, single-workspace app. That keeps the normal
   `conversations.history` rate limits (see ARCHITECTURE.md, "2025 rate-limit
   change").

3. **Copy the token.** On the same page, copy the **User OAuth Token**
   (starts with `xoxp-`). Put it in the **primary checkout's** root `.env`
   (see [`.env.example`](.env.example)):

   ```
   SLACK_USER_TOKEN=xoxp-...
   ```

   Worktrees find this file on their own. Lookup order: `clients/slack/.env`,
   then the working repo root's `.env`, then the primary checkout's `.env`. An
   exported `SLACK_USER_TOKEN` beats all three. If no token is found, the error
   lists every path it searched.

4. **Check it works:** `bin/slack whoami` should print your name.

## Verbs

By default every read verb prints human-readable text. Add `--json` to get
JSONL instead: one contract `Message` per line, with no other fields. Notes
about truncation and skipped items go to **stderr**, so stdout stays clean JSONL.

| Exit code | Meaning |
|---|---|
| 0 | ok |
| 1 | config or API error (missing token, missing scope, not found, ...) |
| 2 | usage error (argparse) |
| 3 | `send` refused because `--confirm` was not given (dry run: preview + `sha256:`) |
| 4 | `send --confirm` refused: `--expect-sha256` missing, malformed or not the text file's hash |

### `whoami`

```
bin/slack whoami
bin/slack whoami --json      # {"source": "slack", "id": "U...", "name": "Don ..."}
```

### `inbox [--since 24h] [--json]`

Shows DMs and group DMs with activity in the window, @-mentions of you, and
new replies in threads you posted in. Accepts `--since` values like `30m`,
`4h`, `24h`, `7d`.

```
bin/slack inbox
bin/slack inbox --since 4h --json
```

`awaiting_reply` is true when the last message in that message's thread is
not from you, and the message is a DM to you or mentions you. Raw responses
are saved under `.cache/raw/slack/<UTC timestamp>/`, and the path is printed
on stderr.

### `thread <permalink|CHANNEL:ts> [--json]`

```
bin/slack thread 'https://example.slack.com/archives/C000GENERAL/p1793718000000100'
bin/slack thread C000GENERAL:1793718000.000100 --json
```

A reply's permalink (one with `?thread_ts=`) opens the whole thread.

### `channel <name|id> [--since 24h] [--limit 50] [--json]`

```
bin/slack channel general --since 24h
bin/slack channel C000GENERAL --limit 20 --json
```

### `search <query> [--json]`

Uses Slack's search syntax (`from:`, `in:`, `after:YYYY-MM-DD`, ...). Returns
up to 50 newest matches.

```
bin/slack search 'deploy plan after:2026-11-01'
```

### `user <id|@name>`

```
bin/slack user U000ALICE
bin/slack user @alice        # matches handle, display name or real name
```

### `normalize <raw_dir> [--json]`

Offline. Re-renders a saved raw directory without touching the network.

```
bin/slack normalize .cache/raw/slack/20261104T180000Z --json
bin/slack normalize clients/slack/tests/fixtures/raw/inbox --json
```

### `send --channel C --thread-ts T --text-file F [--confirm --expect-sha256 H]`

```
bin/slack send --channel D000ALICE --thread-ts 1793786400.000100 --text-file draft.txt
#   -> prints a DRY RUN preview (channel, thread, full text) and `sha256: <H>`,
#      exits 3, sends nothing

bin/slack send --channel D000ALICE --thread-ts 1793786400.000100 --text-file draft.txt \
  --confirm --expect-sha256 <H>
#   -> posts AS YOU via chat.postMessage and prints the new message's permalink
```

* Text comes only from `--text-file`. There is no inline `--text`.
* Without `--confirm`, no client is built and no network call happens.
* **Approved-text hash.** `--confirm` needs `--expect-sha256` set to the digest
  the dry run printed. The file is read once (it must be a regular file, or a
  symlink to one; a FIFO, device or directory is refused, and a FIFO cannot
  hang the read), the bytes read are hashed and compared before any network
  call, and exactly those bytes are posted. A missing or different hash exits
  4 and sends nothing, so text that changed after Don saw it is never posted.
* Every option may be given once. A repeat (`--channel A --channel B`) is a
  usage error (exit 2): argparse would otherwise keep the last one.
* Flags cannot be abbreviated, so `--conf` does not count as `--confirm`.
* `--thread-ts` is optional. Leave it out to post at the top level of the
  conversation.
* If Slack returns a server error or times out during a send, the client does
  **not** retry, because the message may already have posted. Check the
  conversation before sending again.

## Rate limits

Slack sets limits per method, per workspace, per app. The ones that matter
here:

* `search.messages` and `conversations.list`: Tier 2, about 20 per minute.
* `conversations.history` and `.replies`: Tier 3, about 50 per minute.
* `users.info`: Tier 4, about 100 per minute.
* `chat.postMessage`: about 1 per second per channel.

When Slack returns HTTP 429 or `ratelimited`, the client sleeps for
`Retry-After` seconds and retries, up to 5 times. It gives up and reports an
error if Slack asks for a wait longer than 120 s. A default `inbox` run makes
at most about 1 + 10 + 50×3 + 6 + 40 + 200 calls. Each fan-out has a cap, and
anything cut off by a cap is listed as `truncated` on stderr and in
`manifest.json`. See ARCHITECTURE.md for the caps.

## Safety

* The token is read from the environment or `.env`. It is never logged,
  printed, written to the raw cache, or included in errors or `repr`.
* Auth and scope errors (`invalid_auth`, `token_revoked`, `missing_scope`, ...)
  are never retried. `missing_scope` names the scope to add.
* `send` is the only write. It needs `--confirm --expect-sha256 <H>`, and the
  harness's outbound guard hook asks Don before any `bin/slack send` runs,
  showing the text and whether its hash matches `H`.

## Development

```
cd clients/slack
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

All tests run offline against synthetic fixtures in `tests/fixtures/raw/`.
