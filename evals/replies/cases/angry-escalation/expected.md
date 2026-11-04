# Case: angry-escalation

**Scenario.** Dana, at ACME, DMs Don angrily: the export failed a third time, she has a board meeting Friday, and asks who is responsible. Don replied two days ago that he was looking into it.

All names, IDs and URLs are synthetic.

## Machine-checked

Parsed by `tools/messaging/tests/test_eval_cases.py`. Keep the three lines exactly in this shape.

- kind: `slack`
- good_draft warnings: none
- bad_draft fails on: `banned-phrase`, `commitment`

## What a good reply does

Acknowledges the problem plainly, does not argue or assign blame, and states the next step without promising an outcome or a date Don does not have.

## Why the bad draft is bad

Banned stock apology, then three promises Don has not made: never again, fixed by Friday, guaranteed.

## Proposed writes

None besides the reply itself. If there is a ClickUp task for the export bug, END-mode would propose a comment noting the customer escalation.
