---
description: End-of-day wrap-up. Drafts replies for unanswered NEEDS YOU items, proposes ClickUp comments for the day's observations, lists unpushed branches, and stops at "Proposed writes" and "Drafted replies" tables.
argument-hint: "[--since <START time or window>]"
---

# /end-day: drain the day before you close it

A thin invocation of the **`day-cycle`** agent in **END** mode. The procedure lives in
`.claude/agents/day-cycle.md`. You can also say "wrap up" or "end of day".

Arguments: `$ARGUMENTS` (optional `--since`; the default is since this session's
`/start-day`, otherwise 24h).

## Steps

1. Delegate to the `day-cycle` agent: "END mode. $ARGUMENTS".
2. Show its **Drafted replies** and **Proposed writes** tables as returned.
3. **Stop and wait for Don.** Nothing has been sent or written.
4. For each row Don approves, by number, one at a time:
   - **Slack or ClickUp reply:** follow `/reply` steps 5–7 (approve the exact text, write
     it to `$SCRATCH/reply.txt`, run the send command with `--confirm`, report the
     result). The outbound hook will ask Don again, which is expected.
   - **ClickUp comment or status change:** show the exact comment text and command, get
     Don's yes for that row, then run it. Comment before status, always.
   - **`git push`:** run exactly the proposed command, then show the result.
   - A row Don edits gets re-linted (`bin/check-draft`) and shown again before it runs.
   - Stop at the first failure and report it. Don't retry blindly.

## What it will not do

- Send or write anything Don hasn't approved, row by row, with the exact text visible.
- Create ClickUp tasks (no contract verb). It proposes that Don does.
- Push, merge or delete branches on its own, or commit for Don.
- Treat approval of one row as approval of the others.

## Related

- `/start-day`: the other bookend.
- `/reply <permalink|id>`: the send path every approved reply goes through.
