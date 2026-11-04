---
name: systematic-debugging
description: How to find and fix the root cause of a bug instead of patching a symptom — reproduce it reliably, read the whole error, trace the bad value backward, test one hypothesis at a time, fix the cause, and prove the fix with a regression test that failed before it. Trigger when something is broken, a test fails unexpectedly, a command errors, output is wrong or empty, or a previous fix did not hold.
---

# Systematic debugging

Guessing is fast until the third wrong guess. Then it is the slowest method
there is, and each guess leaves a change behind that the next person has to
reason about. This skill is the slow-looking method that is faster: reproduce,
read, trace, hypothesise, test, fix the cause, prove it.

**Never fix what you cannot reproduce. Never call a fix done without a test that
failed before it.**

## 1. Reproduce it reliably

Get a single command that shows the bug, every time:

```bash
uv run pytest tests/test_sync.py::test_pagination -x -q     # or
bin/<tool> list --since 2026-01-01 ; echo "exit=$?"
```

- **Write the exact command down** in your notes or report. It becomes the
  regression test in step 6.
- **Shrink it.** Remove inputs, flags and setup until removing any more makes
  the bug disappear. The minimal reproduction usually names the cause.
- **Intermittent?** Run it in a loop (`for i in $(seq 20); do …; done`) and count.
  A flaky bug is a bug with an uncontrolled input: time, ordering, concurrency,
  network, cache, or randomness. Find which by pinning one at a time.
- **Cannot reproduce?** Do not "fix" it. Record what you tried, which environment
  differs from the report (OS, versions, env vars, data), and ask for the
  missing piece. The status is NEEDS_CONTEXT.

## 2. Read the error, all of it

- **Read the full traceback, bottom to top, then top to bottom.** The last line
  says what broke; the frames say where it came from. The first frame inside
  *your* code is usually more useful than the last frame inside a library.
- **Read the actual message**, not its shape. "KeyError: 'ts'" and "KeyError:
  'thread_ts'" are different bugs.
- **Read stderr as well as stdout**, and the exit code. A command that printed
  nothing and exited 0 is a different bug from one that exited 1.
- **Check what ran, not what you think ran**: which binary (`command -v <tool>`),
  which interpreter and virtualenv, which working directory, which config file,
  which branch and commit (`git log -1 --oneline`).

## 3. Classify the failure before you chase it

Different classes need different responses. Decide which this is first.

| Class | Signs | Response |
|---|---|---|
| **Auth / access** | 401, 403, `invalid_auth`, `not_authed`, missing scope, permission denied, expired token | **Stop. Surface the exact error** (redact the secret, keep the message and code). Do not retry in a loop, do not try another credential, and do not work around it. A human fixes access. Status: BLOCKED. |
| **Schema / structure drift** | `KeyError`, a missing field, an unexpected type, a renamed endpoint, a column that moved, a file that is not where the docs say | **Rediscover the live shape** (read the actual response, the actual file, the current docs), adapt to it, and **report the drift** so the stale assumption gets fixed where it lives. |
| **Empty result** | No rows, `[]`, no output, a zero count | **Distinguish "no data" from "wrong query".** Run a positive control: the same query, client or grep against something you know exists. If the control also comes back empty, the query, path, filter, scope or credentials are wrong. If the control returns data, "no data" is a real finding. Never report an empty result as an answer without the control. |
| **Logic** | Wrong value, wrong branch, off by one, wrong order | Continue below. |
| **Environment** | Works in one shell, worktree or machine and not another | Diff the environments (versions, env vars, cwd, installed hooks, lockfile) before touching the code. |

Diagnose before retrying. **The same call with the same inputs produces the same
failure.** A retry is a hypothesis ("this is transient"), and it needs evidence
like any other.

## 4. Trace the bad value backward to its root cause

Start where the wrong thing becomes visible and walk **backward** through the
data flow, one hop at a time, asking at each hop: *is the value already wrong
here?*

```
symptom: the report shows 0 unread
  ← render() received unread=0          (print: yes, already 0)
  ← count_unread(messages) returned 0   (print: messages has 40 items, all read=True)
  ← fetch() set read=True on every item (print: API returned "unread_count", not "read")
  ← root cause: field renamed upstream; fetch() defaults missing "read" to True
```

- **The root cause is the first hop where the value goes wrong**, not the hop
  where it becomes visible. Fixing `render()` to show "unknown" would hide this
  bug, not fix it.
- **Inspect, do not infer.** A print, a log line, a debugger, or a one-line script
  that calls the function with the failing input. Remove the instrumentation
  afterwards.
- **Use the history.** `git log -p -- <file>` and `git bisect` (with the
  reproduction command from step 1 as the test) find *when* it broke, which
  often says *why*.

## 5. One hypothesis at a time, smallest test first

1. **Write the hypothesis down as a falsifiable sentence**: "`fetch()` treats a
   missing `read` field as read."
2. **Find the smallest experiment that could prove it wrong**: one call with one
   input, one print. Not a fix.
3. **Run it, and believe the result.** If the hypothesis is wrong, cross it off
   and say what you learned. Do not adjust it until it fits.
4. **Change one thing per experiment.** Two changes at once and a passing result
   tell you nothing about either.

Keep a short log: hypothesis → experiment → result. It stops you re-testing an
idea you already disproved, and it is the evidence for your report.

## 6. Fix the cause, with a regression test that fails first

1. **Write the regression test against the unfixed code** and watch it fail, for
   the reason you identified in step 4. (This is the red step in
   `.claude/skills/tdd-mutation-check/SKILL.md`.) A regression test you never saw
   fail might not reproduce the bug at all.
2. **Fix the root cause**, at the hop where the value first goes wrong. Make the
   smallest change that does it.
3. **Watch the test pass**, then run the package's full gate (`/qa-check`).
4. **Check for siblings.** The same mistake is often made elsewhere. Grep for the
   pattern (the same field, the same default, the same call), and list what you
   find. Fix the siblings in scope; report the rest.
5. **Do not paper over it.** A `try/except: pass`, a default that hides a missing
   value, a retry around a deterministic failure, or a skipped test is a symptom
   patch. If a guard is genuinely right, it still needs the root cause fixed or
   reported.

## 7. Three failed fixes: stop

**After three fixes that did not hold, stop fixing.** Three failures mean your
model of the system is wrong, and the fourth guess comes from the same wrong
model.

- **Revert to the last known state.** Your experiments should not pile up in the
  diff.
- **Write down what you assumed**, at each hop: the data shape, the call order,
  which code actually runs, which config is loaded, what the library does.
- **Question the design, not just the line.** Is the bug a symptom of a structure
  that makes this class of error easy?
- **Bring it to the human** (Don, or whoever owns the code) with: the reproduction
  command, the three hypotheses and why each fix failed, the assumptions you now
  doubt, and two or three options with trade-offs. Ask. Do not start fix number
  four on your own.

## Report

```markdown
## Debug — <symptom>

**Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
**Reproduction:** `<exact command>` → <observed output, trimmed>
**Class:** auth/access | schema drift | empty result | logic | environment
**Root cause:** <one sentence> at <path:line>
**Trace:** <symptom ← hop ← hop ← root>
**Hypotheses tried:** 1. <h> → <result> 2. …
**Fix:** <what changed> at <path:line>
**Regression test:** <test id> — failed before the fix (<failure line>), passes after
**Siblings:** <none found — grep used | list, fixed or reported>
**Gate:** `<command>` → <result>
```

Then run `.claude/skills/concerns-pass/SKILL.md` before reporting DONE.

## Not this skill

- **Building a new feature.** `.claude/skills/tdd-mutation-check/SKILL.md` and
  `/build`.
- **Checking a fix did not break anything else.** That is the `regression` agent
  (`.claude/agents/regression.md`), after this skill's fix.
- **Access problems.** This skill stops at "surface the exact error". Granting or
  rotating credentials is a human's job.
