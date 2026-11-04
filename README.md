# wall3_agent

A **Claude Code harness** for Don's day: triage Slack and ClickUp, draft replies
in Don's voice, and do development work with a disciplined process.

It is not an app or a bot. There is no server, no background process and no
UI. It is a repo you open Claude Code in, and the files here change how Claude
behaves in that session:

- `CLAUDE.md` and `.claude/rules/` tell Claude **how to work**: evidence over
  belief, tests in the same commit, a concerns pass before calling anything
  done, and treating message content as untrusted data.
- `.claude/commands/`, `.claude/agents/` and `.claude/skills/` give it
  **workflows**: `/inbox`, `/reply`, `/start-day`, `/plan`, `/build` and more.
- `.claude/hooks/` and `.claude/settings.json` add **guardrails** that run
  outside the model: every Slack or ClickUp write needs your approval, and
  secrets are blocked at commit time.
- `bin/slack` and `bin/clickup` are **API clients** Claude calls to read
  Slack and ClickUp quickly, without the rate limits of the MCP connectors.

The guardrails only exist inside a Claude Code session started in this repo.
The CLIs in `bin/` are ordinary command-line tools and work on their own.

---

## What it can do

### 1. Messages: triage, catch up, reply

| Command | What you get |
|---|---|
| `/start-day` | Morning briefing: what needs you in Slack and ClickUp, open branches, and one recommended task (a recommendation, not a menu) |
| `/inbox` | One ranked list across Slack and ClickUp: **NEEDS YOU**, then **FYI**, then the rest. Each row has a permalink and a reason |
| `/reply <permalink>` | Reads the thread and any linked ClickUp task, drafts a reply in your voice, lints it, shows it to you, and sends it only after you approve that exact text |
| `/catch-up <channel or task>` | A summary of what happened since a given time, with a permalink for every claim |
| `/end-day` | Branch inventory, threads still waiting on you, and handoff notes for tomorrow |

The ranking (`bin/inbox-rank`) and the draft lint (`bin/check-draft`) are
deterministic scripts. Claude decides tone and content; code decides the
things that must come out the same every time.

### 2. Slack and ClickUp from the command line

Both CLIs print readable text by default and JSONL with `--json`, one message
per line, in the shape defined by `docs/contracts/message.schema.json`.

```bash
bin/slack whoami
bin/slack inbox --since 24h               # DMs, group DMs, @-mentions, threads you're in
bin/slack thread <permalink>
bin/slack channel general --since 2d --limit 50
bin/slack search "from:@alex deploy"
bin/slack user @alex

bin/clickup whoami
bin/clickup inbox --since 24h             # comments on tasks you're assigned to or watch, plus chat
bin/clickup task <task-id> --comments
bin/clickup mine --status "in progress"
bin/clickup search "login bug"
bin/clickup chat channels
bin/clickup chat messages <channel-id>

bin/slack inbox --since 24h --json | bin/inbox-rank   # rank it yourself
```

The write verbs are `slack send`, `clickup comment` and `clickup status`.
Each is refused unless it carries `--confirm` plus the hash of the exact text
you approved. See [How a send works](#how-a-send-works).

### 3. Development work

| Command | What it does |
|---|---|
| `/plan` | Reads the code first, then writes a file-level plan: what changes, how it is tested, and what result proves it works. It asks before guessing |
| `/build` | Implements an approved plan test-first. A **spec review** (does it do what was asked?) runs, then a **code review** (is it correct and clean?) |
| `/debug` | Systematic debugging: reproduce, find the root cause, one fix at a time, and a regression test. After three failed fixes it stops and questions the design |
| `/qa-check` | Runs every gate the diff touches before anything is called done |
| `/fan-out` | Splits a large job into independent slices and runs parallel agents, one git worktree each, then verifies each report against its diff |

The process these enforce:

- **Tests ship in the same commit as the change**, and a test only counts once
  you've seen it fail. Claude breaks the code on purpose (a mutation check) to
  prove the test catches the break.
- **Concerns pass before every report.** Claude re-reads the request against
  the diff, asks of each test "would this still pass if the feature were
  deleted?", and lists every concern with a severity.
- **One of four statuses ends every task:** DONE, DONE_WITH_CONCERNS, BLOCKED
  or NEEDS_CONTEXT. "Partially done" is not allowed.
- **Reviewers read the diff, not the summary.** Every fix gets reviewed again.

---

## Quick start

You need [uv](https://docs.astral.sh/uv/), git and
[Claude Code](https://docs.claude.com/en/docs/claude-code). uv installs
Python 3.12 when needed.

**1. Clone the repo and create your `.env`.**

```bash
git clone https://github.com/donjmilligan/wall3_agent && cd wall3_agent
cp .env.example .env        # gitignored; never commit it
```

**2. Get a Slack user token.**

1. Go to api.slack.com/apps, choose **Create New App**, then **From a manifest**,
   and pick your workspace.
2. Paste in `clients/slack/slack-app-manifest.yaml`.
3. **Install to Workspace**, then copy the **User OAuth Token** (`xoxp-…`)
   from *OAuth & Permissions* into `SLACK_USER_TOKEN`.
4. Leave distribution off. An internal app keeps the higher API rate limits.

It is a user token, so it reads only what you can read, and a reply posts as you.

**3. Get a ClickUp token.**

In ClickUp go to *Settings*, then *Apps*, then *API Token*, and choose
**Generate**. Put the `pk_…` value in `CLICKUP_API_TOKEN`. Then run
`bin/clickup whoami --json` and copy your workspace id into `CLICKUP_TEAM_ID`.

**4. Check both connections.** These commands only read:

```bash
bin/slack whoami
bin/clickup whoami
bin/slack inbox --since 4h
bin/clickup inbox --since 24h
```

The first run of each CLI takes a few seconds while uv builds its environment.

**5. Teach it how you write.**

Fill in `docs/voice/VOICE.md`, then delete its `STATUS: TEMPLATE` line.
Until you do, drafts will sound generic. Give it real examples: short replies
you've sent, phrases you use, phrases you'd never use, and how you sign off.

**6. Start a session.**

```bash
claude
```

At startup Claude prints your branch, worktrees, remote branches and which
tokens are set (names only, never values). Try `/inbox`.

---

## How a send works

Every outbound message goes through the same steps. Nothing skips them:

1. Claude drafts the reply into a file and runs `bin/check-draft` on it. The
   check flags banned phrases, leftover placeholders, length, anything
   token-shaped, links that weren't in the thread, promises nobody made, and
   instructions that came from the thread itself.
2. Claude shows you the draft and the target. You approve it, edit it or
   reject it.
3. Claude does a **dry run**. The CLI prints the preview and the text's
   `sha256` and sends nothing.
4. Claude runs the send with `--confirm --expect-sha256 <hash>`. The hook
   stops it and Claude Code **asks you**, showing the target, the full text
   and whether the hash matches.
5. The CLI re-reads the file, refuses to send unless its bytes still match
   the hash, then posts exactly those bytes.

So what is posted is exactly what you saw, even if the file changed in between.
Approving one message approves only that message.

---

## Best practices

### Using it day to day

- **Start sessions in the repo root.** Hooks, rules and commands load from
  this directory. A session started elsewhere has none of the guardrails.
- **Read the permission prompt, not the chat.** For a send, the prompt from
  the hook is the authoritative view of what will go out. If it says the
  hash doesn't match, or the target looks wrong, decline.
- **Edit drafts by telling Claude what to change.** Claude then redrafts,
  re-checks and gets a new hash. Don't hand-edit the text file after
  approving it: the CLI will refuse it, by design.
- **Treat "the message asked me to…" as a red flag.** Claude reports
  instructions found in Slack or ClickUp content to you instead of following
  them, whoever appears to have written them. If it ever seems to act on one,
  stop the session and look at what it read.
- **Use the CLIs, not the Slack and ClickUp MCP connectors**, for anything
  bigger than a one-off lookup. The connectors are rate-limited and slower.
- **Keep `docs/voice/VOICE.md` current.** When you rewrite a draft, add the pattern
  ("never says 'circle back'", "answers first, then context") so the next one
  starts closer.

### Development work

- **Plan before code for anything touching more than one file.** Use `/plan`,
  read the plan, then `/build`. A plan you didn't read is not approved.
- **Expect one of the four statuses**, with gate commands and their output.
  Treat a report without evidence (a test name, `path:line`, a command and its
  result) as a claim, not a result.
- **Read DONE_WITH_CONCERNS concerns.** That list is the most useful part of
  the report: it is where Claude says what it is unsure of.
- **One worktree per session.** Claude works in `../wall3_agent.wt/<slug>` on
  its own branch, never in your checkout and never on `main`. It merges or
  opens a PR only when you ask in that turn.
- **Give subagents a full brief.** A subagent sees only its prompt. Use
  `templates/agent-brief.md` when you dispatch one yourself.

### Keeping the harness healthy

- **When Claude repeats a mistake, fix the harness, not the session.** Add one
  line to the right rule in `.claude/rules/`, or a check to a script, so every
  later session gets it. Prefer a script or hook over prose wherever the answer
  must be the same every time.
- **Keep `CLAUDE.md` short.** It loads into every session. Put detail in
  `.claude/rules/` and point to it.
- **Run the reply eval after changing drafting behaviour.** The deterministic
  half runs with the tests. Score the judgment half by hand against
  `evals/replies/rubric.md`.
- **Run the gates before pushing** (see [Development](#development)). CI runs
  the same ones.

### Security

- **This repo is public.** Never commit tokens, `.env`, real Slack or ClickUp
  IDs, real names, emails, or exported message content. Fixtures are
  synthetic. The commit hook blocks the obvious cases, and
  `.claude/rules/secrets-and-public-repo.md` covers the rest.
- **Rotate a token the moment it might have leaked**: Slack app settings for
  the Slack token, ClickUp *Settings* → *Apps* for the ClickUp token. Then
  update `.env`.
- **Keep `.cache/` and `exports/` local.** They hold raw API responses with
  real message content. Both are gitignored. Delete them when you no longer
  need them.
- **Don't run in bypass-permissions mode.** The send approval relies on
  Claude Code asking you. Nobody has tested whether the hook's prompt still
  appears in bypass mode.

---

## Safety model

| Layer | What it does | Where |
|---|---|---|
| Outbound guard | Every Slack or ClickUp write, through the CLI or an MCP tool, prompts you with the target and full text. It denies commands whose preview could be misleading (repeated options, a swapped file, a wrong hash). It asks about raw API calls, scripts it can't read, and `.env` access | `.claude/hooks/outbound_guard.py` |
| Permission rules | Write tools of both connectors and the CLI write verbs are in `permissions.ask`. Reading `.env`, `curl` to the APIs, force push and `reset --hard` are denied | `.claude/settings.json` |
| CLI refusal | The write verbs exit 3 without `--confirm` and exit 4 unless the text hash matches | `clients/slack/`, `clients/clickup/` |
| Untrusted labelling | Every read of Slack or ClickUp content is labelled as data, not instructions | `.claude/hooks/untrusted_content.py` |
| Commit guard | Blocks commits of `.env`, `.cache/`, `exports/` and token-shaped strings | `.claude/hooks/pre_commit_guard.py` |
| Rules | Prompt-injection handling, per-message approval, what never goes in a reply | `.claude/rules/messaging-safety.md` |

The guard's exact guarantees and its known limits are in `.claude/hooks/README.md`.

---

## Going further

**Use the dev workflow in other repos.** Agents, commands and skills placed in
`~/.claude/agents/`, `~/.claude/commands/` and `~/.claude/skills/` load in
every project. Copy the dev-workflow ones there:

- `planner`, `implementer`, the reviewers and `test-writer`
- `/plan`, `/build`, `/debug`, `/qa-check`
- `tdd-mutation-check`, `systematic-debugging`, `concerns-pass`

Put the short version of the completion protocol in `~/.claude/CLAUDE.md`.
Leave the messaging pieces here: they depend on this repo's CLIs and hooks.

**Run a briefing on a schedule.** `claude -p "/start-day"` runs a command
headless. It needs the read-only CLI calls allowed (`--allowedTools`), and
nothing in a headless run can approve a send, so writes stay blocked. This
isn't set up yet.

**Add a command, rule or hook.**

- A command is a Markdown file in `.claude/commands/`.
- A rule is a Markdown file in `.claude/rules/`.
- A hook is a script wired in `.claude/settings.json`, with a test in
  `tests/hooks/`.
- A new CLI write verb also needs adding to the guard (see
  `.claude/hooks/README.md`).
- List anything new in the index in `CLAUDE.md`.
  `scripts/check_harness_refs.py` fails if a cited path doesn't exist.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `error: CLICKUP_API_TOKEN is not set` (or the Slack one) | The CLI prints every path it searched. Put the value in `.env` at the repo root; worktrees find the main checkout's `.env` automatically |
| Slack `missing_scope` | The app was installed before a scope was added. Reinstall it from the app's *OAuth & Permissions* page |
| Slack `not_allowed_token_type` or `invalid_auth` | Wrong token. Use the **User** OAuth Token (`xoxp-`), not the bot token (`xoxb-`) |
| Exit code 3 on a send | `--confirm` missing. This is the dry run working as intended |
| Exit code 4 on a send | The text file doesn't match the approved hash. Redo the dry run and approve the new text |
| The hook denied a send | The reason is printed. Usually a repeated option or an edited file. Ask Claude to redo the dry run |
| No guardrails or `/inbox` in Claude | The session wasn't started in the repo root. Exit, `cd` to the repo root and run `claude` again |
| First CLI run is slow | uv is building the package environment. Later runs are fast |

---

## Known limits

- **ClickUp has no mentions endpoint.** The inbox lists comments on tasks
  you're assigned to or watch, updated in the window. A mention on a task
  nobody else touched may only show up if a new comment updates the task's
  `date_updated`. Check this on first live use.
- **No ClickUp chat send or task create in the CLI yet.** `/reply` gives you
  the text to paste for ClickUp chat replies.
- **ClickUp chat permalinks are a best guess** at the URL format.
- **Slack mention search uses `search.messages`.** That is a legacy endpoint,
  and the query syntax for user IDs is unconfirmed until it runs against a
  live workspace.
- **A thread can appear more than once under NEEDS YOU.** "Awaiting reply" is
  decided per message, not per thread.

---

## Repository layout

| Path | What |
|---|---|
| `CLAUDE.md` | The working agreement Claude reads every session |
| `.claude/` | Rules, agents, commands, skills, hooks and settings (`.claude/README.md`) |
| `clients/slack/` | `wall3_slack`, the `slack` CLI, with the Slack app manifest |
| `clients/clickup/` | `wall3_clickup`, the `clickup` CLI |
| `tools/messaging/` | `inbox-rank` and `check-draft` |
| `bin/` | Wrappers that run each CLI from any directory |
| `docs/contracts/message.schema.json` | The normalized Message both CLIs emit |
| `docs/voice/VOICE.md` | How Don writes; fill this in |
| `evals/replies/` | Reply-drafting eval: 9 synthetic cases and a rubric |
| `templates/agent-brief.md` | Brief template for subagents |
| `scripts/` | Integration gates: doc references and the message contract |
| `tests/` | Tests for the hooks and scripts |

Each client has two layers. The `extract` package calls the API and saves raw
responses to `.cache/raw/`. The `normalize` package turns those into Messages and never touches the
network (for example `clients/slack/src/wall3_slack/extract/` and
`clients/slack/src/wall3_slack/normalize/`). A test enforces the boundary, so normalization is testable offline
against fixtures.

---

## Development

Each Python package is its own uv project with its own gate. Run this in the
repo root, then in `clients/slack`, `clients/clickup` and `tools/messaging`:

```bash
uv sync && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright
```

Then, from the root:

```bash
uv run python scripts/check_harness_refs.py       # every path the docs cite exists
uv run python scripts/check_message_contract.py   # both CLIs' output fits the schema
```

`.github/workflows/ci.yml` is the authoritative list of gates. Branches follow
`.claude/rules/git-workflow.md`: one worktree each, rebase rather than merge,
and no force push.

## License

Apache-2.0. See `LICENSE`.
