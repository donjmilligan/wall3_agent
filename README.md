# wall3_agent

A Claude Code-native agent harness: a repo you run `claude` in. It helps its
owner, Don, do three things:

1. **Triage and draft replies** to Slack and ClickUp messages. Claude drafts;
   Don approves every send.
2. **Read Slack and ClickUp through their REST APIs** with two local CLIs,
   `bin/slack` and `bin/clickup`. These replace the rate-limited MCP connectors.
3. **Do development work with discipline**: plans, tests in the same commit,
   mutation checks, and an explicit completion protocol.

`CLAUDE.md` is the working agreement Claude follows. `.claude/` holds the
rules, agents, commands, skills and hooks (`.claude/README.md`).

## Layout

| Path | What |
|---|---|
| `clients/slack/` | `wall3_slack`, the `slack` CLI (uv package) |
| `clients/clickup/` | `wall3_clickup`, the `clickup` CLI (uv package) |
| `tools/messaging/` | `wall3_messaging`, which provides `inbox-rank` and `check-draft` (uv package) |
| `bin/` | Thin wrappers that run the CLIs from any directory |
| `docs/contracts/message.schema.json` | The normalized Message both CLIs emit |
| `docs/voice/VOICE.md` | How Don writes, used when drafting |
| `evals/replies/` | Evals for reply drafting |
| `scripts/` | Integration gates (doc references, message contract) |
| `tests/` | Tests for the hooks and scripts |

## Setup

You need [uv](https://docs.astral.sh/uv/), Python 3.12 (uv can install it),
git, and Claude Code.

```bash
git clone https://github.com/donjmilligan/wall3_agent && cd wall3_agent
uv sync                      # root dev tooling (pytest, ruff, pyright, jsonschema)
cp .env.example .env         # gitignored; fill in the three values below
```

1. **Slack.** Create a Slack app from the manifest in `clients/slack/`
   (api.slack.com/apps, then *Create New App*, then *From a manifest*). Install it to
   your workspace and copy the **User OAuth Token** into `SLACK_USER_TOKEN`.
   It is a user token, so the CLI reads what you can read.
2. **ClickUp.** In ClickUp go to *Settings*, then *Apps*, then *API Token*. Generate a
   token and put it in `CLICKUP_API_TOKEN`. Put your workspace id in
   `CLICKUP_TEAM_ID`. It is the number that follows the domain in any workspace URL.
3. Check that both work:

   ```bash
   bin/slack whoami --json
   bin/clickup whoami --json
   ```

When a session starts, `.claude/hooks/session_start.py` reports which of the
three variables are set. It prints names only, never values.

## Running it

Run `claude` in the repo root. Then:

| Command | Does |
|---|---|
| `/start-day` | Morning briefing: inbox, open branches, one recommended task |
| `/inbox` | Fetch Slack and ClickUp, rank them, and summarise what needs you |
| `/reply` | Draft a reply in your voice, check it, and send it only if you approve |
| `/catch-up` | Summarise a channel, thread or task since a given time |
| `/end-day` | Branch inventory, unanswered threads, handoff notes |
| `/plan` | Plan a code change before writing it |
| `/build` | Implement an approved plan, with tests |
| `/fan-out` | Run parallel agents on independent slices, one worktree each |

You can also run the CLIs directly. `CLAUDE.md` lists every verb:

```bash
bin/slack inbox --since 24h --json | bin/inbox-rank
bin/clickup task <id> --comments
```

## Safety model

- **Nothing goes out without your approval.** Every write needs you to approve
  that exact message and its target: a Slack send, a ClickUp comment or status
  change, or any write through an MCP connector. Two independent layers make
  Claude Code prompt you:
  - `.claude/hooks/outbound_guard.py` catches CLI write verbs however they are
    invoked, and every non-read Slack or ClickUp MCP tool. The prompt shows the
    target, the full text and whether it matches the approved hash; a command
    whose preview could be wrong (repeated options, a swapped file, ...) is
    denied. Heuristics ask about raw API calls, `.env`, and scripts the hook
    cannot read. Limits: `.claude/hooks/README.md`.
  - `permissions.ask` in `.claude/settings.json` lists every known write tool
    of the Slack and ClickUp connectors, and the CLI write commands.

  The CLIs also refuse to write without `--confirm` (exit 3), and refuse
  `--confirm` unless `--expect-sha256` matches the hash of the text file that
  the dry run printed (exit 4). So what is posted is exactly the text Don saw.
- **Message content is data, not instructions.** Whatever a Slack message or
  ClickUp comment says, Claude reports any instructions in it to you and never
  acts on them. `.claude/hooks/untrusted_content.py` labels every read to
  reinforce this. Full rules: `.claude/rules/messaging-safety.md`.
- **This repo is public.** `.env`, `.cache/`, `exports/` and token-shaped
  strings are gitignored, and `.claude/hooks/pre_commit_guard.py` also blocks
  commits that contain them. Test fixtures are synthetic. See
  `.claude/rules/secrets-and-public-repo.md`.
- **Destructive git is denied.** That covers force push, `reset --hard` and
  `--no-verify`. Claude works in its own worktree per session and opens PRs
  only when you ask (`.claude/rules/git-workflow.md`).

## Development

Each package has its own gate, run from its own directory. The root project
covers the hooks and scripts:

```bash
uv sync && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run python scripts/check_harness_refs.py       # every path the docs cite exists
uv run python scripts/check_message_contract.py   # CLI output matches the Message schema
```

`.github/workflows/ci.yml` is the authoritative list of gates.

## License

Apache-2.0. See `LICENSE`.
