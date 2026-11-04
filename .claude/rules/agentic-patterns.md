---
description: Evidence discipline, mutation checks, fault tolerance and safety gates for agent work.
alwaysApply: true
---

# Agentic patterns

## Evidence discipline

- Every factual claim carries a re-checkable artifact: a path:line, a command
  and its output, a test name. "I believe" is not evidence.
- Verify live artifacts, not summaries. Check your own work adversarially:
  ask "how would this fail?", not "does this look right?".
- A grep *count* is not a claim about content — read the matches. An ID, SHA
  or path cited from memory is unverified until you read it back.

### Prove a negative with a positive control

"Nothing calls X" or "no message mentions Y" needs the same search, in the same
form, returning something that does exist. Without the control, an empty result
is indistinguishable from a typo, a wrong path or a search that never ran.

### A green test is not evidence until you have seen it go red

Break the change on purpose (flip a condition, drop a branch, weaken a regex,
change an exit code) and confirm a test fails for the right reason, then
revert. If nothing fails, the test is not testing that behaviour — say so.

Three shapes produce tests that cannot fail:

1. **An assertion whose conclusion is an absence** (`not in`, `== []`, "no
   output"). Absence is also what breakage produces. Pair it with a presence
   assertion proving the path ran.
2. **A check measuring the half that cannot vary** — an ID that never drifts,
   a stale server, the test runner's view instead of the app's.
3. **An assertion between two values where one is computed from the other.**
   Pin the thing a human decided, not the thing derived from it.

The diagnostic for all three: *what would this test do if the feature were
deleted?* If "pass", it is not a test of that feature.

### When a mutation escapes, say which of three things happened

| What happened | Response |
|---|---|
| The assertion could not fail for any input | fix the **assertion** |
| The assertion is sound but no fixture reaches the mutated branch | add the **fixture** |
| The mutation is genuinely behaviour-preserving | say so, and why |

## Discover before asserting

- Check a load-bearing assumption (field name, API shape, file path, flag)
  against live state before using it.
- A missing expected element is a finding, not a crash: report it and adapt.

## Fault tolerance

- Classify failures: (a) auth/access → surface the exact error and stop;
  (b) shape drift → re-discover, adapt, report the drift; (c) empty result →
  distinguish "no data" from "wrong query".
- Never return a confident wrong answer. Loud failures beat silent ones.
- Diagnose before retrying: the same call with the same input fails the same way.

## Safety gates

Before any irreversible action — send, delete, push, status change:

1. State what you are about to do.
2. List what it affects.
3. Say whether it can be undone.
4. Act only after confirmation.

Order side effects reversible first, irreversible last, and confirm each one
succeeded before the next. Messaging has a stricter version of this:
`.claude/rules/messaging-safety.md`.

## Subagents

A subagent knows only its brief. Brief it like a peer: goal, what is ruled
out, files to read, scope, and the report format
(`templates/agent-brief.md`, `.claude/skills/subagent-briefs/SKILL.md`). Its
report is a claim — verify the load-bearing parts before relaying them.
