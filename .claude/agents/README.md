# Agents

Subagents are specialists you call when a task matches; they are not a
pipeline that runs on every task. Each runs in its own context window and knows
only its brief — write the brief with `templates/agent-brief.md`
(`.claude/skills/subagent-briefs/SKILL.md`). Its report is a claim: verify the
load-bearing parts before relaying them.

## Messaging

| Agent | Purpose |
|---|---|
| `.claude/agents/inbox-triage.md` | Rank Slack + ClickUp items that need Don, with a reason and permalink each |
| `.claude/agents/reply-drafter.md` | Draft one reply in Don's voice from the thread context; never sends |
| `.claude/agents/day-cycle.md` | Start-of-day and end-of-day sweep: inbox, open branches, task status |

## Development

| Agent | Purpose |
|---|---|
| `.claude/agents/planner.md` | Turn a request into a file-level plan with tests and an outcome |
| `.claude/agents/implementer.md` | Build one planned slice, tests first, in its own worktree |
| `.claude/agents/spec-reviewer.md` | Check a diff against the request, requirement by requirement |
| `.claude/agents/code-reviewer.md` | Review a diff for correctness, clarity and test strength |
| `.claude/agents/regression.md` | Trace a change's blast radius and run targeted regression tests |
| `.claude/agents/security-reviewer.md` | Look for secrets, injection, unguarded writes and public-repo leaks |
| `.claude/agents/test-writer.md` | Write tests that fail when the feature is deleted, with mutation evidence |

## When to delegate

- Default: do the work yourself.
- Delegate when the task matches an agent's specialty and its checklist or
  output format helps, or to run independent work in parallel.
- Never auto-chain agents unless asked.

## Adding an agent

A `.md` file here with frontmatter:

```yaml
---
name: my-agent
description: Use when ... (this text decides when Claude delegates)
tools: Read, Grep, Bash
---
```

Then add a row above and in `CLAUDE.md`.
