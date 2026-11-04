# Case: status-from-clickup-task

**Scenario.** Carol DMs Don asking where the ACME export bug stands. The linked ClickUp task has a comment from Bob with the root cause, the PR, and the expected merge timing.

All names, IDs and URLs are synthetic.

## Machine-checked

Parsed by `tools/messaging/tests/test_eval_cases.py`. Keep the three lines exactly in this shape.

- kind: `slack`
- good_draft warnings: none
- bad_draft fails on: `length`

## What a good reply does

Answers from the task, not from memory: cause, PR link, and the timing Bob actually stated, attributed to him. Two sentences plus the task link.

## Why the bad draft is bad

Over the Slack length limit. It also pads with investigation detail nobody asked for and invents a "by Wednesday at the latest" date that the task does not support (rubric: invented facts).

## Proposed writes

None besides the reply itself.
