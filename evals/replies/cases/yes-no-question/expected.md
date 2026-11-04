# Case: yes-no-question

**Scenario.** Alice asks in a DM whether she can merge a README-only docs PR today. A yes/no question.

All names, IDs and URLs are synthetic.

## Machine-checked

Parsed by `tools/messaging/tests/test_eval_cases.py`. Keep the three lines exactly in this shape.

- kind: `slack`
- good_draft warnings: none
- bad_draft fails on: `banned-phrase`

## What a good reply does

Answers the question in the first word. One line. No preamble, no restating the question.

## Why the bad draft is bad

Opens with a banned filler phrase ("Great question") and hedges a simple yes.

## Proposed writes

None besides the reply itself.
