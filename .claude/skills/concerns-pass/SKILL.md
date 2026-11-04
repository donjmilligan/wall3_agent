---
name: concerns-pass
description: How to run the mandatory structured self-review before reporting DONE or DONE_WITH_CONCERNS — map every requirement to evidence, walk the diff hunk by hunk, prove each test can fail, name the failure modes, list what was not verified, check safety, then classify honestly. Trigger when any agent or session is about to report a development task complete, when verifying another agent's report, or when asked "are we done?".
---

# Concerns pass

The last thing that happens before a completion report, every time. It exists
because the most common failure in agent work is not bad code. It is a
confident report about code that does something slightly different from what
was asked, backed by tests that would pass if the feature were deleted.

The pass is a **structured** review, not a vibe check. Seven steps, each with a
concrete artifact. It turns "I think this is done" into a list of claims that
each point at something re-runnable, and an explicit list of what is not.

The completion statuses themselves (DONE / DONE_WITH_CONCERNS / BLOCKED /
NEEDS_CONTEXT) are defined in `.claude/rules/completion-protocol.md` and
`CLAUDE.md`. This skill is how you earn one.

## When it runs

- **Every agent, before reporting DONE or DONE_WITH_CONCERNS.** The implementer,
  the test-writer, a session working alone, a `/debug` fix.
- **The orchestrator, when verifying a report** (`/build`, `/fan-out`). It
  re-runs steps (a), (c) and (e) against the pushed branch rather than trusting
  the report's copy of them.
- Not for BLOCKED or NEEDS_CONTEXT reports. Those state what is missing instead.

**Self-review is required and it is not review.** Running this pass does not
replace `spec-reviewer` and `code-reviewer`. It makes their job shorter and your
report honest. An author reviewing their own diff shares every blind spot that
produced it, which is why the steps below are mechanical where they can be.

**A concern you can fix inside scope is not a concern. It is unfinished work.**
If the pass turns up a missing test, an unhandled empty input, or an unmapped
requirement that is in your brief, fix it, then run the pass again. The concerns
list is for things you could not or should not resolve in this task, never for
things you chose not to finish.

## Inputs

Have these open before you start:

1. **The original request, verbatim.** The brief, the plan item, the task
   description, the user's message. Not your memory of it and not your summary.
2. **The base.** Usually `origin/main`, or the branch you are stacked on.
   Run `git fetch origin` first, so the base is current.
3. **The full diff:** `git diff <base>...HEAD` (three dots, so you see only
   what this branch changed), and `git diff --stat <base>...HEAD`.
4. **Your gate and mutation output**, as captured text, not as recollection.

If you cannot produce input 1 verbatim, stop: the status is NEEDS_CONTEXT.

## (a) Requirements → evidence

Re-read the request **line by line**. Split it into atomic requirements: every
sentence that says *do*, *must*, *should*, *never*, *only*, *including*, a
named file, a named output, a named format. Scoping words ("only", "just",
"exactly", "never") are requirements in their own right.

For each one, cite the evidence in the diff as `path:line`, plus the test that
proves it, plus where you saw that test go red.

```
| # | Requirement (quoted)                          | Evidence (file:line)            | Test that proves it            | Seen red? |
|---|-----------------------------------------------|---------------------------------|--------------------------------|-----------|
| 1 | "reject an empty channel name with exit 2"    | src/cli.py:41-44                | tests/test_cli.py::test_empty  | yes, M1   |
| 2 | "never print the token"                       | src/auth.py:18 (redact())       | tests/test_auth.py::test_redact| yes, M2   |
| 3 | "document the new flag in the README"         | —                               | —                              | UNMAPPED  |
```

- **Unmapped = concern.** Either do it now (it was asked for) or it is a reason
  the task is not DONE.
- **Mapped with no test = concern**, unless the requirement is genuinely
  untestable (prose, a config value). Say which.
- **"Done in spirit" is not a mapping.** If you changed the requirement's
  meaning to fit what you built, that is a spec gap, and the reviewer will find
  it.

## (b) The diff, hunk by hunk: "why is this here?"

Walk every hunk of `git diff <base>...HEAD`. For each, one line: which
requirement in (a) it serves.

- **A hunk that serves no requirement is a scope concern.** Reformatting an
  untouched function, a renamed variable "while I was there", a new helper used
  once, a dependency bump, a refactor of adjacent code. Revert it, or list it as
  a concern with the reason it had to stay.
- **Files you were told not to touch.** Check the brief's do-not-touch list
  against `git diff --name-only <base>...HEAD`. Any hit is HIGH and usually means
  revert.
- **Leftovers.** Debug prints, commented-out code, `TODO` you added, skipped or
  `xfail` tests you added, widened type ignores or lint suppressions. Each is
  either removed or listed.
- **Generated or binary files** in the diff that the task did not call for.

The reviewer will ask "why is this here?" about every hunk. Ask it first.

## (c) Each test: "what would it do if the feature were deleted?"

For every new or changed test, answer that question. If the answer is "pass",
the test does not test the feature.

Then confirm, from your recorded mutation log, that at least one deliberate
mutation of the production code turned this test red **for the right reason**
(the assertion failed, not an import error or a typo). The method and the
recording format are in `.claude/skills/tdd-mutation-check/SKILL.md`.

Check for the three vacuous shapes, which that skill describes with examples:

1. **The conclusion is an absence** (`assert x not in y`, `== []`, "not
   called"). It needs a paired positive assertion proving the path ran.
2. **It measures the half that cannot vary**: a stale build, an installed copy
   instead of the source, an identifier instead of the behaviour, a mock asserting
   on itself.
3. **Both sides of the assertion come from the same computation.** It is
   arithmetic, so it holds by construction.

A test with no recorded red is listed under NOT VERIFIED, by name.

## (d) Failure modes: where does it break?

You must be able to say where your code breaks. "I couldn't find any problems"
means you did not look. For each changed unit, walk these and write down what
happens:

| Probe | Ask |
|---|---|
| Inputs | Empty, `None`/missing, whitespace, unicode, very long, wrong type, malformed (bad JSON, truncated file) |
| Size | Zero items, one item, the page boundary, 10 000 items, a payload bigger than memory or a rate limit |
| Errors | What happens when the network call, file read, subprocess, or third-party API fails, times out, or returns an unexpected shape? Is the error surfaced or swallowed? |
| Concurrency | Two runs at once, two worktrees, a retry after partial success; is anything non-idempotent? |
| State | First run with nothing cached, a stale cache, a missing config or env var |
| Time | Timezones, DST, "today" at midnight, clock skew, a token that expires mid-run |
| Environment | Another OS, another shell (zsh does not word-split unquoted variables), no network |

Each probe ends in one of three ways: **handled and tested** (cite the test),
**handled but untested** (concern, usually MED), or **not handled** (fix it if
it is in scope; otherwise a concern, with severity by likelihood × damage).

## (e) Evidence check: every claim has an artifact

Draft the report, then go through it claim by claim. Every factual sentence
("tests pass", "lint is clean", "handles empty input", "no other callers") needs
an artifact someone else can re-run: a command and its output, a `file:line`, a
grep and its result, a commit SHA you read back with `git log`.

- **Anything you did not run is NOT VERIFIED**, listed by name in the report,
  never left implied. Typical entries: the slow lane, a gate that could not run
  in this environment, a platform you did not test on, behaviour against the
  real third-party API.
- **An asserted absence needs a positive control.** "No other callers" is
  `grep -rn 'fn_name(' src/` → 1 hit (the definition) *and* the same grep for a
  name you know is called → N hits. Without the control, an empty grep is
  indistinguishable from a wrong path.
- **A grep count is not a claim about content.** Read the matches.
- **IDs from memory are unverified.** Read the SHA, branch, task ID or path back
  from the live source before you put it in the report.
- **The gate is the repo's gate, as CI runs it.** Read it out of the CI
  workflow (`/qa-check` does this), not out of prose. A gate that could not run
  is NOT VERIFIED, not "passed".

## (f) Safety

| Check | How |
|---|---|
| **Secrets** | `git diff <base>...HEAD` for tokens, keys, passwords, `.env` content, auth headers, cookies. `git status --ignored` that `.env` and credential files stay ignored. Rules: `.claude/rules/secrets-and-public-repo.md`. |
| **Public-repo leakage** | Is the repo public? (`gh repo view --json visibility`.) If so: no real names beyond those the repo already uses, no emails, no internal channel, task, workspace or user IDs, no hostnames or IPs, no customer or company specifics, no personal absolute paths, in code, tests, fixtures, comments or commit messages. |
| **Outbound side effects** | Does any new code path send a message, post a comment, change a task status, send an email, call a webhook, or write to a third-party API? Each must be gated by explicit human approval (`.claude/rules/messaging-safety.md`), and tests must not reach the real service. |
| **Irreversible operations** | Deletes, overwrites, force-pushes, migrations, `rm -rf`, history rewrites. Each must be stated, confirmed, and reversible-first. |
| **Untrusted text** | Does third-party content (a Slack message, a task description, a web page) reach a shell, a file path, a query, or an LLM prompt as instructions? It must stay data. See `.claude/agents/security-reviewer.md`. |

Any hit here that is not already gated is HIGH at minimum. A committed secret is
BLOCKED until rotated, whatever else is true: removing it in a later commit does
not remove it from history.

## (g) Classify

Give every concern a **severity** and a **proposed follow-up** (an action and an
owner, not "consider looking into").

| Severity | Meaning |
|---|---|
| **HIGH** | Could make the change wrong or unsafe in normal use, or leaves a load-bearing claim unverified (a required gate not run, a security path untested). |
| **MED** | A real failure mode or edge case that is unhandled or untested but unlikely in normal use; unrequested scope that is harmless but should not have been bundled. |
| **LOW** | Naming, docs, a cosmetic nit, a hardening idea. |

Then pick exactly one status:

- **No concerns** → **DONE.** Every requirement mapped with seen-red evidence,
  every gate green, nothing NOT VERIFIED that the task depends on.
- **Concerns, none of which block correctness of what was asked** →
  **DONE_WITH_CONCERNS**, listing each with severity and follow-up.
- **A requirement is unmet, a test was never seen red on a core requirement, or
  a required gate is red** → **not DONE.** Keep working. If you cannot proceed,
  **BLOCKED** (what blocks you, what you already tried) or **NEEDS_CONTEXT**
  (exactly what you need).

"Partially done" is not a status. Neither is "DONE, but…": if there is a
"but", it is DONE_WITH_CONCERNS or it is not done.

**Never launder a concern.** Downgrading a severity, rewording an unmet
requirement as a "follow-up", or moving a NOT VERIFIED item into prose so the
status reads better defeats the pass. The reviewer re-runs this, and a
laundered concern is a worse finding than an honest one.

## Report template

Copy this, fill every section, and delete nothing. A section with nothing in it
says `none`, so a reader can tell "none" from "forgot".

```markdown
## Concerns pass — <task id or one-line title>

**Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
**Branch:** <branch> @ <short sha, read back with git log -1>
**Base:** <base ref> @ <sha>   **Diff:** <N files, +A −D>   (git diff --stat <base>...HEAD)

### (a) Requirements → evidence
| # | Requirement (quoted) | Evidence (file:line) | Test | Seen red? |
|---|---|---|---|---|
| 1 | "<verbatim>" | <path:line> | <test id> | yes — M<n> |

Unmapped: <none | list>

### (b) Scope
Every hunk maps to a requirement above: <yes | no — list the hunks that don't, and why each stays>
Do-not-touch paths touched: <none | list>
Leftovers (debug, TODO, skips, suppressions): <none | list>

### (c) Test strength
| Test | If the feature were deleted it would… | Mutation that turned it red |
|---|---|---|
| <test id> | fail: <assertion> | M<n>: <what you changed> → <failure line> |

Vacuous-shape check: <clean | list, with which shape>

### (d) Failure modes
| Where | Input / condition | What happens | Covered by |
|---|---|---|---|
| <path:line> | <empty / timeout / …> | <behaviour> | <test id | UNTESTED> |

### (e) Evidence
| Claim | Artifact (command → result) |
|---|---|
| Gate green, <package> | `<exact command>` → <last line of output> |

**NOT VERIFIED:** <none | each item, and why it could not be run>

### (f) Safety
Secrets: <clean — how checked>   Public-repo leakage: <clean — how checked>
Outbound side effects: <none | each, and its approval gate>
Irreversible ops: <none | each>   Untrusted text → shell/LLM: <none | each, and how it stays data>

### (g) Concerns
| # | Severity | Concern | Proposed follow-up (action, owner) |
|---|---|---|---|
| 1 | HIGH/MED/LOW | <what> | <what to do, who> |

**Needs a human:** <none | the decision or action, and for whom>
```

## Worked example (abridged)

Brief: *"Add a `--since` flag to the list command. Accept ISO dates only. Do not
change the default output."*

- (a) Three requirements: the flag exists, ISO-only, default output unchanged.
  The third is easy to miss because it asks for nothing new. It maps to an
  existing snapshot test that still passes, which proves nothing until a
  mutation (changing the default sort) turns it red. It did: M3.
- (b) One hunk reorders imports in an untouched file. The formatter did it.
  Reverted.
- (c) `test_since_rejects_bad_date` asserts `exit_code != 0`. If the flag were
  deleted, argparse would also exit non-zero ("unrecognized argument"). It
  passes for the wrong reason: shape 2. Fixed to assert the specific error text.
  Re-mutated: deleting the validation now turns it red for the right reason.
- (d) `--since 2026-03-08` crosses a DST boundary for the local timezone. The
  code parses dates as naive. Concern, MED: follow-up to decide on UTC vs local,
  for Don.
- (e) The slow lane (against the real API) was not run. NOT VERIFIED.
- (g) **DONE_WITH_CONCERNS**: one MED (timezone semantics), one NOT VERIFIED
  (slow lane).

## Not this skill

- **Running the gates.** `/qa-check` discovers and runs them; this pass checks
  that you did and cites the output.
- **Writing the tests or doing the mutation.** That is
  `.claude/skills/tdd-mutation-check/SKILL.md`. This pass audits the record.
- **Independent review.** `spec-reviewer` and `code-reviewer`
  (`.claude/agents/`) review the diff with no stake in it. The concerns pass is
  the author's half, and it never substitutes for theirs.
- **Defining the statuses.** `.claude/rules/completion-protocol.md` does.
