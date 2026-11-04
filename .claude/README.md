# .claude/ — Claude Code configuration

Claude Code reads this directory automatically when you run `claude` in the
repo. `CLAUDE.md` at the root is the working agreement; this is the machinery.

| Path | What it holds | Loaded |
|---|---|---|
| `.claude/rules/` | Behavioural rules with frontmatter (`alwaysApply` or `globs`) | Automatically |
| `.claude/agents/` | Subagents — see `.claude/agents/README.md` | On delegation |
| `.claude/commands/` | Slash commands — see `.claude/commands/README.md` | `/name` |
| `.claude/skills/` | Pattern docs — see `.claude/skills/README.md` | `/name` or by relevance |
| `.claude/hooks/` | Event hooks — see `.claude/hooks/README.md` | By event |
| `.claude/settings.json` | Hook wiring, `permissions.ask` / `permissions.deny` | Committed, shared |
| `.claude/settings.local.json` | Your own overrides (env, extra allows) | Gitignored, never committed |

## Settings

`.claude/settings.json` is shared and committed. It:

- wires the five hooks (all `python3 "${CLAUDE_PROJECT_DIR}/.claude/hooks/<name>.py"`);
- puts every known Slack/ClickUp write tool, and the CLI write verbs
  (`bin/slack send`, `bin/clickup comment`, `bin/clickup status`), under
  `permissions.ask`, so each one prompts even if you allow-list broadly;
- denies `rm -rf`, force pushes, `git reset --hard` and `--no-verify`.

Rule syntax follows https://code.claude.com/docs/en/permissions: Bash rules
are `Bash(<command> *)` (a trailing ` *` also matches the bare command), MCP
rules are the full tool name with no parentheses, and deny → ask → allow is the
evaluation order. A PreToolUse hook's "allow" never overrides a matching ask or
deny rule.

For your own setup create `.claude/settings.local.json` (gitignored) — e.g.
allow read-only commands you run often. Never add an `allow` for a write verb.

## Rules

| Rule | Loads |
|---|---|
| `.claude/rules/core-rules.md` | always |
| `.claude/rules/agentic-patterns.md` | always |
| `.claude/rules/completion-protocol.md` | always |
| `.claude/rules/messaging-safety.md` | always |
| `.claude/rules/git-workflow.md` | always |
| `.claude/rules/secrets-and-public-repo.md` | always |
| `.claude/rules/task-lifecycle.md` | always |

Add a rule: a `.md` file here with frontmatter `description` plus
`alwaysApply: true` or `globs: <pattern>`, then list it in this table and in
`CLAUDE.md`. `scripts/check_harness_refs.py` fails if a listed path is missing.
