# Case: scheduling-ask

**Scenario.** Bob mentions Don in #acme-project asking for 30 minutes this week; he offers Thursday or Friday afternoon.

All names, IDs and URLs are synthetic.

## Machine-checked

Parsed by `tools/messaging/tests/test_eval_cases.py`. Keep the three lines exactly in this shape.

- kind: `slack`
- good_draft warnings: none
- bad_draft fails on: `placeholder`

## What a good reply does

Accepts the meeting, uses the options Bob gave, and leaves the exact slot to the calendar instead of inventing Don's availability. Claude has no calendar access, so when presenting the draft it tells Don to change the day if he already knows it.

## Why the bad draft is bad

Template residue (`<day>`, `{{time}}`) would reach Bob verbatim.

## Proposed writes

None besides the reply itself.
