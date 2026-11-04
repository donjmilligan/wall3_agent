---
description: Task lifecycle on ClickUp — plan, comment the plan, then move status; every status change carries a comment; agents never set Done.
alwaysApply: true
---

# Task lifecycle

ClickUp is the durable record of who is doing what and why. A board nobody can
trust is worse than none, so every status it shows carries a reason a human can
read. All writes below follow `.claude/rules/messaging-safety.md` — each one is
drafted and approved by Don before it is posted.

## 1. Plan before anything

Read the code, the contract and the task. Ask questions while they are cheap.
Write the plan: what changes, in which files, tested how, verified by what
outcome. If you can't name the outcome, you aren't ready to start. For
multi-file work use `/plan` (`.claude/commands/plan.md`).

## 2. Check it isn't already taken

Read the task's status, assignee and recent comments
(`bin/clickup task <id> --comments`), and check
`git ls-remote --heads origin` for a branch on the same topic. A matching
branch or an In Progress task means it is taken: say so, and pick something
else or ask.

## 3. Comment the plan, then move the status

Post the plan as a comment on the task — what, why, which branch, how it will
be verified — then move it to In Progress (read the list's real status names;
don't assume them). `bin/clickup status <id> <status> --comment-file F` posts
the comment first, then the status.

**Every status change carries a comment first**: intent, reason or result.
No silent flips. If the plan changes materially mid-task, comment the change
and why before continuing.

## 4. Do the work

In your own worktree on your own branch (`.claude/rules/git-workflow.md`).

## 5. Report the result

Comment the result: what was done against the plan, branch name and that it
is pushed, the gate evidence. A result without evidence is a claim.

## 6. Review — never Done

**Agents never set a task to Done.** The ceiling is the list's review status.
Ready → move to review with the result comment. Not ready or unsure → keep it
In Progress and comment the concerns. A human moves it to Done.

## Observations go on a task, not in chat

Anything not being fixed right now — a deferred bug, a follow-up, a trap that
cost an hour, a number that means something other than it looks — goes on the
ClickUp task it belongs to. Chat scrolls away; the task is where the next
session will look. Test: would a session tomorrow have to rediscover this? Then
it goes on a task.

## Dedup before creating

Search before creating a task — by symptom, in words a stranger would use, not
only your own phrasing (`bin/clickup search "<query>"`). Found a match →
comment on it. No match → propose the new task to Don; create it only once Don
agrees it is real scope. Never silently delete or close a duplicate: mark it,
link both, and leave the close to a human.
