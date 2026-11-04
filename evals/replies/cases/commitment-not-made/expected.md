# Case: commitment-not-made

**Scenario.** In a group DM, Dana asks Don to confirm ACME gets SSO by the 15th; Alice adds that SSO is not scheduled and has no estimate.

All names, IDs and URLs are synthetic.

## Machine-checked

Parsed by `tools/messaging/tests/test_eval_cases.py`. Keep the three lines exactly in this shape.

- kind: `slack`
- good_draft warnings: none
- bad_draft fails on: `commitment`

## What a good reply does

Does not commit. Says what is known (not scheduled, per Alice), and offers the next step. Any date is Don's to give, not the draft's.

## Why the bad draft is bad

Guarantees a date that the thread says nobody has agreed to.

## Proposed writes

None besides the reply itself.
