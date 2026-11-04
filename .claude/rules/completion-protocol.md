---
description: The four completion statuses and the mandatory concerns pass before reporting any of them.
alwaysApply: true
---

# Completion protocol

## The four statuses

End every task with exactly one:

| Status | Means | Must include |
|---|---|---|
| **DONE** | Every step complete. Tests in the diff. | Evidence for every claim; gate commands and result lines. |
| **DONE_WITH_CONCERNS** | Complete, with issues Don should know. | Each concern with severity and a proposed follow-up. |
| **BLOCKED** | Cannot proceed. | What blocks, what was already tried, what would unblock it. |
| **NEEDS_CONTEXT** | Missing information. | Exactly what is needed and why it changes the approach. |

**"Partially done" is not a status.** Unfinished work is BLOCKED or
NEEDS_CONTEXT with the remainder named.

A DONE report never moves a task to Done (`.claude/rules/task-lifecycle.md`).

## The concerns pass — mandatory before any status

Run it every time, including for BLOCKED and NEEDS_CONTEXT. Procedure in
`.claude/skills/concerns-pass/SKILL.md`; the minimum is:

1. **Request vs diff, line by line.** Re-read the original request (not your
   memory of it). For each requirement write: met / not met / changed, with the
   file:line or command output that shows it. Read the diff
   (`git diff origin/main...HEAD`), not your summary of it.
2. **Each test: "what would it do if the feature were deleted?"** If the answer
   is "pass", it is not a test. Record the mutation you ran and the test that
   went red.
3. **Grep the diff** for secrets, real IDs, emails, private names and message
   content (`.claude/rules/secrets-and-public-repo.md`).
4. **List every concern** with severity and a follow-up:
   - **HIGH** — wrong behaviour, data or secret exposure, an unguarded write,
     a requirement not met.
   - **MED** — a gap a reviewer would want fixed before merge, or an
     assumption that could be wrong.
   - **LOW** — polish, naming, docs, follow-ups that can wait.

"No concerns" is itself a claim: say what you checked to reach it.

## Report shape

```
STATUS: DONE_WITH_CONCERNS
Branch: feature/x @ <sha> (pushed)
What changed: <files, one line each>
Evidence: <gate commands + result lines; mutation -> red test>
Concerns:
  [MED] <concern> -> <follow-up>
Next action: <one line>
```
