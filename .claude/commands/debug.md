---
description: Debug a symptom systematically — reproduce, read the full error, classify, trace to the root cause, fix it with a regression test that failed first
argument-hint: "<symptom: failing command, test id, error text, or wrong output> [repo=<abs path>]"
---

# /debug — find the root cause, then fix it

Runs `.claude/skills/systematic-debugging/SKILL.md` on the symptom in
`$ARGUMENTS`. The skill holds the method; this command sets it up and holds the
line on the rules that are easy to skip under pressure.

Arguments: `$ARGUMENTS`
- The symptom: a failing command, a test ID, an error message, or a description
  of wrong output.
- `repo=<abs path>`: where to debug. Default: the current git repo.

## Steps

1. **Load the skill** (`.claude/skills/systematic-debugging/SKILL.md`) and follow
   it in order. The steps below are the checkpoints.
2. **Work where it is safe.** Investigation (reading, running, printing) can
   happen anywhere. **A fix happens in a worktree on a `fix/<slug>` branch**,
   created outside the repo root (`<repo-parent>/<repo>.wt/<slug>`), never in the
   primary checkout and never on main (`.claude/rules/git-workflow.md`).
3. **Checkpoint: reproduction.** Show Don the exact command and its output before
   proposing any fix. Cannot reproduce → report NEEDS_CONTEXT with what you tried.
4. **Checkpoint: classification.** Auth/access error → **stop**, surface the exact
   error (secret redacted), report BLOCKED. Do not retry, switch credentials or
   work around it. Empty result → run the positive control before calling it "no
   data". Schema drift → rediscover the live shape and report the drift.
5. **Checkpoint: root cause.** State it in one sentence with `file:line`, and the
   backward trace that found it, before writing the fix.
6. **Checkpoint: regression test first.** The test fails against the unfixed code,
   for the identified reason, before the fix is written
   (`.claude/skills/tdd-mutation-check/SKILL.md`).
7. **Fix, gate, concerns pass.** Smallest root-cause fix; `/qa-check`; then
   `.claude/skills/concerns-pass/SKILL.md`.
8. **Three failed fixes → stop** and bring Don the reproduction, the hypotheses,
   the assumptions in doubt, and options. Do not try a fourth.
9. **Hand-off for non-trivial fixes.** If the fix touches 3+ files, a public
   interface, or a shared module, finish at the root cause and the failing
   regression test, then offer `/build` for the fix so it gets both review stages
   (and the `regression` agent).
10. **Report** with the skill's report block. Commit and push the fix branch if
    one was made; no PR unless Don asks.

## What it will not do

- Propose a fix for a bug it has not reproduced.
- Retry or work around an auth or access error.
- Report an empty result as "no data" without a positive control.
- Patch the symptom (swallow the error, add a default, skip the test) and call it fixed.
- Write the fix before the regression test has failed.
- Keep guessing after three failed fixes.
- Fix on main, in the primary checkout, or open a PR unasked.

## Related

- `.claude/skills/systematic-debugging/SKILL.md`: the method.
- `.claude/agents/regression.md`: what else the fix might have broken.
- `/build`: for fixes big enough to need review.
- `/qa-check`: the gate.
