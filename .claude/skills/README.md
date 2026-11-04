# Skills

A skill is reference knowledge for doing a recurring thing correctly — "here is
how", where a command is "do this now". Each lives in
`.claude/skills/<name>/SKILL.md` with `name` and `description` frontmatter;
invoke with `/name` or let Claude load it by relevance.

| Skill | Purpose |
|---|---|
| `.claude/skills/reply-voice/SKILL.md` | How Don writes; applies `docs/voice/VOICE.md` to a draft |
| `.claude/skills/thread-context/SKILL.md` | Gather the thread, links and history a reply needs |
| `.claude/skills/tdd-mutation-check/SKILL.md` | Red-green-refactor plus mutation evidence that tests can fail |
| `.claude/skills/systematic-debugging/SKILL.md` | Reproduce, hypothesise, bisect, fix with a regression test |
| `.claude/skills/concerns-pass/SKILL.md` | The mandatory pre-report pass in `.claude/rules/completion-protocol.md` |
| `.claude/skills/subagent-briefs/SKILL.md` | Write a brief a subagent can act on, using `templates/agent-brief.md` |

## Adding a skill

Create `.claude/skills/<name>/SKILL.md`:

```yaml
---
name: my-skill
description: How to do X. Use when Y.
---
```

Document the pattern, the gotchas, how to test it, and what it is *not* for.
List it above and in `CLAUDE.md`.
