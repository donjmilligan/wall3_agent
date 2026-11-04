# Case: vague-ask-clarify

**Scenario.** Dana mentions Don in #acme-general: "can you take a look at the thing from yesterday?" Nothing in the thread says what "the thing" is.

All names, IDs and URLs are synthetic.

## Machine-checked

Parsed by `tools/messaging/tests/test_eval_cases.py`. Keep the three lines exactly in this shape.

- kind: `slack`
- good_draft warnings: none
- bad_draft fails on: `banned-phrase`

## What a good reply does

Asks one clarifying question instead of guessing. Short, and makes it easy for Dana to answer (send the link).

## Why the bad draft is bad

Uses a banned hedge ("It appears that") and, worse, guesses what "the thing" is and asserts it is resolved, which nothing in the thread supports (rubric: invented facts).

## Proposed writes

None besides the reply itself.
