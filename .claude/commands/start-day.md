---
description: Start-of-day briefing. Ranked Slack and ClickUp inbox, open dev work from git, and one recommendation for what to do first. Read-only.
argument-hint: "[--since 24h]"
---

# /start-day: what is waiting, what is in flight, where to start

A thin invocation of the **`day-cycle`** agent in **START** mode. The procedure lives in
`.claude/agents/day-cycle.md`, so there is one copy to maintain. You can also just say
"start my day".

Arguments: `$ARGUMENTS` (optional `--since <window>` for the inbox; the default is 24h,
or 72h on a Monday).

## Steps

1. Delegate to the `day-cycle` agent: "START mode. $ARGUMENTS".
2. Show the briefing as returned.
3. Stop. Don chooses what to do next. The briefing ends with the first command for its
   recommendation (often `/reply <permalink>` or `/build`).

## What it will not do

- Send, comment, change a status, push, or start the recommended work.
- Give a menu. It makes one recommendation, ranked by "unblocks someone else" first.
- Treat a missing token or unreachable API as fatal. It reports it and continues.

## Related

- `/end-day`: the other bookend.
- `/inbox`: just the inbox.
- `/reply <permalink|id>`: act on a waiting item.
