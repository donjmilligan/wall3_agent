# Commands

Slash commands are step-by-step recipes: type `/name` with arguments. Each
ends with verification and a completion status
(`.claude/rules/completion-protocol.md`).

## Messaging

| Command | Purpose |
|---|---|
| `.claude/commands/inbox.md` | `/inbox` — fetch Slack + ClickUp, rank with `bin/inbox-rank`, summarise what needs Don |
| `.claude/commands/reply.md` | `/reply` — gather context, draft, `bin/check-draft`, show Don; send only on approval |
| `.claude/commands/catch-up.md` | `/catch-up` — summarise a channel, thread or task since a given time |
| `.claude/commands/start-day.md` | `/start-day` — morning briefing and one recommended next task |
| `.claude/commands/end-day.md` | `/end-day` — branch inventory, unanswered threads, handoff notes |

## Development

| Command | Purpose |
|---|---|
| `.claude/commands/plan.md` | `/plan` — plan a change (files, tests, outcome) before any code |
| `.claude/commands/build.md` | `/build` — implement an approved plan with tests in the same commit |
| `.claude/commands/fan-out.md` | `/fan-out` — run parallel agents on independent slices, one worktree each |
| `.claude/commands/qa-check.md` | `/qa-check` — run every gate the diff touches, then the concerns pass |
| `.claude/commands/debug.md` | `/debug` — reproduce, hypothesise, test, fix with a regression test |

## Adding a command

A `.md` file here; the filename is the command name. Use `$ARGUMENTS` for
what the user types after it. List it above and in `CLAUDE.md`.
