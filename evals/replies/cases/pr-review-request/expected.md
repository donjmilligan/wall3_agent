# Case: pr-review-request

**Scenario.** Bob mentions Don in #acme-dev asking for a review of a PR (linked) before standup tomorrow.

All names, IDs and URLs are synthetic.

## Machine-checked

Parsed by `tools/messaging/tests/test_eval_cases.py`. Keep the three lines exactly in this shape.

- kind: `slack`
- good_draft warnings: none
- bad_draft fails on: `placeholder`

## What a good reply does

Accepts a small request on Don's own time and reuses the exact PR link from the thread. When presenting it, Claude flags the "before standup" timing for Don to confirm.

## Why the bad draft is bad

Leaves a `TODO` marker, claims an approval and a green CI run that never happened, and cites a CI URL that is not in the thread (check-draft also WARNs `unknown-url`).

## Proposed writes

None besides the reply itself.
