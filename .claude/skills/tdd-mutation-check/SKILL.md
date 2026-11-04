---
name: tdd-mutation-check
description: How to build test-first — write the failing test, watch it fail for the right reason, make it pass with the smallest change, refactor — and then prove each test can fail by mutating the code by hand and recording which test went red. Trigger when implementing any feature or bug fix, writing or auditing tests, or before citing a green test run as evidence.
---

# TDD and the mutation check

**A green test is not evidence until you have seen it go red.** A test that
passes proves only that it passes. It proves the feature works when, and only
when, breaking the feature makes it fail. This skill is how you get both halves:
red/green/refactor while building, and a hand mutation check before you report.

It costs minutes. It catches the defect that every other check misses: the test
that would stay green if the feature were deleted.

## Red → green → refactor

1. **Red.** Write the test first, for one behaviour, before the production code
   exists. Run it. **It must fail, and it must fail for the right reason**: the
   assertion about the behaviour fails. An `ImportError`, a `NameError`, a
   fixture error, or a syntax error is not the right reason. It means the test
   has not yet reached the thing it is testing. Fix the scaffolding until the
   failure is the assertion, then read the failure message and confirm it
   describes the missing behaviour.
2. **Green.** Write the smallest production change that makes that test pass.
   Not the general version, not the next three cases. Run the test, then the
   package's fast suite.
3. **Refactor.** With the tests green, clean up names, duplication and structure,
   in production code and test code alike. Re-run after each step. Refactoring
   changes no behaviour, so no test should change colour.
4. **Repeat** for the next behaviour. Each cycle is small enough that a red you
   did not expect is cheap to understand.

For a **bug fix**, the red step is a regression test that reproduces the bug
against the unfixed code. See `.claude/skills/systematic-debugging/SKILL.md`.
If you cannot make it fail before the fix, you have not found the bug.

**Tests ship in the same commit as the code they test.** "Tests in a follow-up"
means the code shipped untested.

### Keep the test honest

- **Test behaviour through the public surface**: the CLI's exit code and output,
  the function's return value, the file written, not private helpers.
- **Mock at the boundary you do not own**: the HTTP client, the third-party SDK,
  the clock. Never mock the unit under test, and never assert only that a mock was
  called with what you just told it to return.
- **Deterministic or it does not go in the gate lane**: frozen time, fixed
  seeds, no network, no ordering assumptions about sets or dicts, no sleeps.

## The mutation check

After the tests are green, break the production code on purpose and confirm a
test notices. Do this for **every requirement**, not every line: one mutation
per behaviour you claim, at minimum.

### How to mutate by hand

1. **Start clean.** Commit your work first, or at least make sure the file you are
   about to mutate has no other uncommitted change (`git status --short <file>`
   shows nothing). Then `git restore <file>` drops exactly your mutation and
   nothing else.
2. **Make one small, behaviour-changing edit** in the production code the test
   claims to cover. Good mutations:
   - flip a condition (`if not x:` → `if x:`, `>=` → `>`), the classic off-by-one;
   - delete the line that does the work (the validation, the redaction, the write);
   - return early with a plausible wrong value (`return []`, `return None`, the
     input unchanged);
   - swap two arguments, drop an `await`, or catch-and-ignore an exception;
   - change a constant the behaviour depends on.
3. **Run the specific test**, then the package's suite. Read the failure: it should
   be the assertion, describing the broken behaviour.
4. **Restore** with `git restore <file>` (or by reversing the edit), and re-run
   to confirm green again.
5. **Record it.** The record is the evidence. A mutation you did not write down
   did not happen, as far as a reviewer can tell.

```
M1  src/cli.py:42   `if not name:` → `if name:`
    → tests/test_cli.py::test_empty_name_exits_2 FAILED  (assert 0 == 2)
M2  src/auth.py:18  deleted `token = redact(token)`
    → tests/test_auth.py::test_token_never_logged FAILED (assert 'redacted' in ...)
M3  src/list.py:77  sort key `created` → `name`
    → (no test failed) → case 2, see table below; added fixture with
      out-of-order names; re-run → tests/test_list.py::test_default_order FAILED
```

Put the record in the commit message body or the completion report, under
"Mutation evidence". `.claude/skills/concerns-pass/SKILL.md` step (c) checks it.

### Never `git stash`

Do not use `git stash` to set work aside while mutating, or for any other reason
in agent work. **The stash is shared by every worktree of a repository**: a
parallel agent's `git stash pop` can apply your work to its tree, or yours to
yours. Commit, or use `git restore <file>` on a file you know is otherwise clean.
The same goes for `git reset --hard` and `git checkout .`: they discard
everything, not just your mutation.

### Parallel agents: separate scratch directories

If you write a mutation script, a throwaway fixture, or a captured output file,
put it in **your own** scratch directory (the one your brief names, or a fresh
`mktemp -d`), never a shared `/tmp/mutate.py`. Two agents mutating through the
same scratch file overwrite each other's mutations, and both report results that
belong to the other.

## The three vacuous shapes

Nearly every test that passes for the wrong reason has one of these shapes. The
diagnostic for all three: **"what would this test do if the feature were
deleted?"** If the answer is "pass", it does not test the feature.

### 1. The conclusion is an absence

```python
def test_token_not_logged(caplog):
    run_sync(config)
    assert "sk-" not in caplog.text  # passes if run_sync() logged nothing at all
```

Absence is also what breakage produces. If `run_sync` crashed before logging, or
logging is misconfigured in the test, this stays green. **Pair it with a
positive assertion that proves the path ran:**

```python
def test_token_not_logged(caplog):
    run_sync(config)
    assert "sync complete: 3 items" in caplog.text  # the path executed
    assert "sk-" not in caplog.text  # and did not leak
```

Same for `assert calls == []`, `assert not errors`, `mock.assert_not_called()`,
"the field is absent from the response".

### 2. It measures the half that cannot vary

The check runs and succeeds, but it describes something other than the
behaviour:

- the test imports an **installed copy** of the package (a stale `site-packages`
  or an old wheel) instead of the source you changed;
- it asserts on an **identifier that never changes** (the command name, the
  enum key) instead of the value that does (the output, the label);
- it asserts **`exit_code != 0`** where the deleted feature would *also* exit
  non-zero (argparse rejects an unknown flag);
- it asserts **a mock returned what the mock was configured to return**;
- it runs against a **stale server, a missing `.env`, or a worktree without its
  hooks installed**, and so tests that environment, not your code.

Fix it by asserting the specific behaviour: the exact error text, the exact
output, read from the code you changed.

### 3. Both sides come from the same computation

```python
ALLOWED = ALL_SCOPES - BLOCKED_SCOPES
...
def test_every_scope_classified():
    assert ALLOWED | BLOCKED_SCOPES == ALL_SCOPES    # true by arithmetic, always
```

`ALLOWED` is defined as `ALL_SCOPES` minus `BLOCKED_SCOPES`, so the union is
`ALL_SCOPES` whatever either set contains. Adding a dangerous scope to
`ALL_SCOPES` silently allows it, and this test stays green. The same shape:
`expected = render(x); assert render(x) == expected`, or a snapshot regenerated
by the code it checks.

**Fix by pinning what a human decided**, not what the code derived:

```python
    assert ALLOWED == {"read:channels", "read:messages"}   # widening means editing this line
```

The smell: an assertion whose two sides are connected by a line of code elsewhere
in the same module. The sharper diagnostic: *"can I change the value this
assertion is about, in any direction, and still see green?"*

## When a mutation changes nothing: which of three cases?

A mutation that no test catches is **not automatically a vacuous test**. Saying
"mutation escaped" without saying which case it is, is itself an unverified
claim. Decide which one happened, because the fixes differ:

| What happened | How to tell | Response |
|---|---|---|
| **The assertion could not fail, whatever the input** | It is one of the three shapes above. | Fix the **assertion**. |
| **The assertion is sound, but no fixture reaches the mutated branch** | Add a print or breakpoint at the mutation: it never executes, or every fixture takes the same side of it. | Add the **fixture**: an input that discriminates. Do not rewrite the assertion. |
| **The mutation preserves behaviour** | Reasoning shows the change is equivalent (dead code, a redundant guard, an equivalent operator for every reachable input). | Say so, and say why. If the code is dead, that is a finding too. |

The second case is the easy one to misfile as the first. Example: a test asserts
`find_mentions("ping the team", user_id="4") == []`. Mutating the matcher from a
word-boundary match to a substring match changes nothing, because there is no
`"4"` anywhere in the string, so both implementations return `[]`. The assertion
was fine; the input could not tell the two implementations apart. The fix is to
add inputs where `"4"` appears inside another token (`"ping user 142"`, `"in
2024"`), each still expected to return `[]`, plus `"ping 4"` expected to return
`["4"]`. The assertion does not change.

## Checklist

- [ ] Each new test was run before the code existed, and failed on its assertion.
- [ ] Each claimed behaviour has at least one recorded mutation that turned a test red.
- [ ] Every escaped mutation is classified as case 1, 2 or 3 and handled.
- [ ] Every absence assertion has a paired presence assertion.
- [ ] The tests are deterministic, and sit in the right lane (gate vs slow).
- [ ] The tests are in the same commit as the code.
- [ ] No `git stash`; scratch files in my own directory.

## Not this skill

- **Choosing what to test across a module, or auditing coverage gaps.** Use the
  `test-writer` agent (`.claude/agents/test-writer.md`).
- **Finding a bug's cause.** `.claude/skills/systematic-debugging/SKILL.md`; come
  back here for the regression test.
- **Running the repo's full gate.** `/qa-check`.
- **Automated mutation-testing tools.** They are welcome where a repo has one, but
  they do not replace reading the failure of a mutation you chose on purpose.
