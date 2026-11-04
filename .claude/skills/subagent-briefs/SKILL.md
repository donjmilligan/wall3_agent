---
name: subagent-briefs
description: How to write a brief for a fresh subagent that knows only what the brief says — goal and why, ruled-out paths, absolute paths, owned and do-not-touch files, the contract, constraints, gates, lifecycle, stop conditions and report format — and how to verify what comes back. Trigger when dispatching any agent with the Agent tool, running /build or /fan-out, or resuming a blocked agent.
---

# Writing a subagent brief

A fresh agent starts with an empty context. **It knows only the brief.** It has
not seen this conversation, Don's rulings, the files you read, the approach you
rejected, or the reason the task exists. Everything it needs, you write down;
everything you leave out, it guesses.

A brief is not a summary of the task. It is the whole of the agent's world.

The fill-in version is `templates/agent-brief.md`. This skill explains what each
part is for, so you can write one well and judge one you receive.

## What every brief carries

| Part | Why it is there | Write it as |
|---|---|---|
| **Goal and why** | The agent makes dozens of small calls. Knowing the purpose lets it make them the way you would. | One paragraph: the outcome, who it is for, and how it will be judged. Quote the request or the ruling verbatim, with its source. |
| **Ruled out** | Saves the agent rediscovering a dead end, or "improving" your design back into the one you rejected. | "Tried X, failed because Y." "Not Z: Don ruled against it on <date>." |
| **Exact paths** | Agents in other worktrees resolve relative paths against the wrong directory. | Absolute paths for the worktree, scratch directory and every file named. |
| **Owned vs do-not-touch** | Parallel agents collide on files, and scope creeps toward whatever is open. | Two explicit lists. Anything not on the owned list is do-not-touch by default. |
| **Contract** | Consumers and producers built in parallel must agree on a shape neither has seen. | The interface verbatim: function signatures, CLI flags and output, file formats, JSON shapes. Mark it PROPOSED if it is not yet approved. |
| **Constraints** | The rules this repo and this task impose. | Public repo or not; secrets handling; no outbound sends; no PRs, merges or force-push; git rules; language and tooling. |
| **Gates** | The agent cannot know which commands CI runs. | The exact commands with their working directory, read from the CI workflow, not from prose. |
| **Lifecycle** | The order matters: the claim, the early push and the tests-in-same-commit are what make parallel work safe. | Numbered steps, from worktree creation to report. |
| **Stop conditions** | An agent that keeps going past an ambiguity makes a decision that was not its to make. | "Stop and report back, without guessing, if …" |
| **Report format** | A free-form report hides the gaps. A fixed format makes them visible. | The REPORT BACK block, field by field. |

## Principles

**One task per agent.** One task, one branch, one worktree, one report. An agent
given two tasks finishes one and summarises the other. If the work is two tasks,
dispatch two agents, or one after the other.

**Context isolation is the point.** A fresh agent is useful *because* it does not
carry your assumptions, so do not paste them in as facts. Give it the
evidence (paths, commands, quoted rulings) and let it read the code itself.
"The bug is in `parse()`" is a conclusion; "the failing command is X and the
traceback ends at `src/parse.py:88`" is evidence it can check.

**Do not hand an agent your hypothesis to confirm.** An agent asked "confirm
that X is the cause" tends to return X confirmed. Ask the open question ("find
why X fails; here is the reproduction"), or, for a review, give it the diff and
the requirements and not your opinion of them.

**Absolute paths, always.** The agent's shell may reset its working directory
between commands. Write `/abs/path/to/<repo>.wt/<slug>/src/cli.py`, and tell it
to run git as `git -C <worktree> …`.

**Name the scratch directory.** Each agent gets its own. Mutation scripts and
captured output collide when agents share one.

**Write the stop conditions as triggers, not judgment calls.** "Stop if the
change needs a file outside your owned list" works. "Use good judgment about
scope" does not.

**Tell it how it will be checked.** An agent that knows the orchestrator will
read its diff and re-run a mutation writes a different report than one that
thinks its report is the end of the story.

## Verifying what comes back

**The report is a claim, not proof.** It arrives in the same confident register
whether it is right or wrong. Before you relay it or build on it:

1. **Read the diff yourself**: `git -C <repo> fetch origin && git -C <repo> diff
   origin/main...origin/<branch>`. Does it do what the brief asked, and nothing
   else? (`spec-reviewer` does this formally.)
2. **Confirm what it says exists**: the branch is pushed (`git ls-remote --heads
   origin <branch>`), the SHA matches, the commits are there, any tracker comment
   it claims exists.
3. **Re-run at least one gate yourself**, from the command in the brief, in a
   scratch worktree of the pushed branch.
4. **Re-run at least one of its mutations yourself.** If its mutation evidence
   says M1 turns test T red, make M1 in your scratch worktree and watch T fail.
   A mutation that does not reproduce is a HIGH finding.
5. **Check its NOT VERIFIED list against what you need.** Something it could not
   run may be something you must run, or must tell Don about.

A verified report is the one you relay. Say what you checked.

## Blocked or needs context: resume, do not restart

When an agent reports BLOCKED or NEEDS_CONTEXT, **resume that same agent** with
the answer (`SendMessage` to its ID or name). Do not start a fresh one. The
blocked agent holds everything it learned: the files it read, the dead ends, the
half-built branch. A new agent starts from the brief and rediscovers all of it,
or worse, makes a different set of choices on the same branch.

Start a new agent only if the original's context is genuinely wrong (it
misunderstood the task at the root), and then say so in the new brief: what the
first attempt did, what is on the branch, and what to keep.

## Anti-patterns

- **"See the conversation above."** There is none.
- **"Fix the tests."** Which tests, failing how, with what command?
- **"You know the codebase."** It does not.
- **Relative paths, or "the usual place".**
- **A brief that is longer on background than on the contract.** The contract is
  what it builds to.
- **No do-not-touch list.** It will touch something.
- **Dispatching before the plan is approved.** The brief encodes decisions; get
  them made first.
- **Relaying the report unread.**

## Not this skill

- **Deciding how to split work into parallel slices.** `/fan-out`.
- **The review stages a slice goes through after it reports.** `/build`.
- **Self-review by the agent receiving the brief.** `.claude/skills/concerns-pass/SKILL.md`.
