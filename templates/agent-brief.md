# Agent brief — fill-in template

Copy everything inside the fence, fill every `<placeholder>`, and delete
nothing. A line that does not apply says `none`, so the agent can tell "none"
from "forgot". How to write each part well:
`.claude/skills/subagent-briefs/SKILL.md`.

The agent knows only this brief. Use absolute paths everywhere.

```
You are <agent role, e.g. "the implementer for slice 2 of 4">. You know only
this brief: you have not seen the conversation that produced it.

## Goal
<One paragraph: the outcome, who it is for, how it will be judged.>
Why: <the reason this task exists>.
Request, verbatim: "<quote>" — source: <task id / message / plan item>.
Rulings in force: <quote each, with who and when | none>.

## Ruled out
- <approach> — <why it was rejected, by whom | none>

## Where
Target repo:      <absolute path to the repo's primary checkout>
Your worktree:    <absolute path: <repo-parent>/<repo>.wt/<slug>>  (outside the repo root; work only there)
Branch:           <feature|fix|chore>/<slug>, from <base ref> @ <sha>
                  <or: stacked on <branch> @ <sha>>
Scratch dir:      <absolute path, yours alone — mutation scripts and captured output go here>
Run git as:       git -C <your worktree> …

## Scope
You own (may create or edit):
- <absolute path or glob>
Do not touch (anything not listed above is do-not-touch by default):
- <absolute path or glob>
Read for context:
- <absolute path> — <why>

## Contract
<The interface you build to or provide, verbatim: signatures, CLI flags and
output, file formats, JSON shapes. Mark PROPOSED if not yet approved. | none>

## Constraints
- Repo visibility: <public | private>. <If public: no private names, IDs,
  emails, hosts, paths or company specifics anywhere, including tests and
  commit messages.>
- Secrets: never print, log, commit or paste a token. <Where credentials come from.>
- Outbound side effects: <none allowed | exactly which, and the approval gate>.
  Tests never reach a real third-party service.
- Git: no PRs, no merges, nothing to main, no --no-verify, no force-push, no
  reset --hard, no git stash (the stash is shared by every worktree).
  Rebase onto the base, never merge main into your branch.
- <Language, tooling, style rules that apply.>

## Gates (from <CI workflow path>, not from prose)
In <working directory>:  <exact command>
In <working directory>:  <exact command>
Slow lane (do NOT run unless told): <command | none>

## Lifecycle, in order
0. <Your worktree was created for you; confirm with git -C <your worktree> status
   and git -C <your worktree> log -1 --oneline. | Create it:
   git -C <target repo> worktree add <your worktree> -b <branch> <base>.>
   Never nest a worktree inside the repo root.
   <Install dependencies / hooks: exact command | none needed>.
1. <If tracked: comment your plan on <task id> via <tracker CLI>, then move it to
   <in-progress status>. Comment before every status change. Never set Done.
   | Not tracked: skip.>
2. Commit a first (or empty) commit and push early: git -C <your worktree> push -u origin <branch>.
   A pushed branch is how other sessions see the work is taken.
3. Build test-first (.claude/skills/tdd-mutation-check/SKILL.md): watch each new
   test fail for the right reason, then pass. Tests go in the same commit as the code.
4. Mutation-check every requirement: break the code on purpose, record
   "M<n> <file:line> <change> → <test> FAILED (<assertion>)", restore.
5. Run the full gate above. A gate that cannot run is NOT VERIFIED, not passed.
6. Run the concerns pass (.claude/skills/concerns-pass/SKILL.md) against this
   brief and `git diff <base>...HEAD`. Fix what is in scope; list the rest.
7. Commit (imperative subject < 72 chars; end the message with
   "<attribution trailer>") and push.
8. <If tracked: comment the result with evidence on <task id>; move to
   <review status> only if DONE; otherwise comment the concerns and leave it.>
9. Report back (below).

## Stop and report back, without guessing, if
- the change needs a file outside your owned list;
- the contract does not cover what you found, or needs to change;
- a requirement is ambiguous in a way that changes the design;
- a gate fails for a reason outside your diff, or cannot run;
- you hit an auth / access error (report the exact error, redacted);
- three fix attempts at the same failure have not held;
- <task-specific trigger>.
A follow-up you discover is not part of this task: report it, do not start it.

## How you will be checked
The orchestrator will read your diff, confirm the branch and SHA, re-run one
gate and one of your mutations, and send the diff to spec and code review. Report
what is true, including what you did not verify.

## REPORT BACK
Status:          DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
Branch:          <branch> @ <short sha, read back with git log -1>
Commits:         <sha subject> (one per line)
Files changed:   <git diff --stat <base>...HEAD, summary line>
Gates:           <command> → <result line>   (one per gate; NOT VERIFIED + why, if not run)
Mutation evidence:
                 M1 <file:line> <change> → <test> FAILED (<assertion>)
                 <escaped: case 1/2/3, and what you did>
Concerns:        <# | HIGH/MED/LOW | concern | proposed follow-up>  (or none)
NOT VERIFIED:    <list | none>
Tracker:         <comment id / link | not tracked>
Needs a human:   <decision or action, for whom | none>
```
