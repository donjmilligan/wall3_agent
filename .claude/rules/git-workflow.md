---
description: Git workflow — one worktree per session, branch naming, push early, rebase never merge, PRs only on request, no destructive git.
alwaysApply: true
---

# Git workflow

## One worktree per session

A shared checkout has one index and one HEAD; two sessions in it corrupt each
other's staging. Every session that will commit works in its own worktree,
outside the repo root:

```bash
git fetch origin
git worktree add ../wall3_agent.wt/<slug> -b feature/<slug> origin/main
```

- The primary checkout is for Don. Never run git in another session's worktree.
- After the branch merges: `git worktree remove ../wall3_agent.wt/<slug>` and
  `git branch -d <branch>`.

## Branches

- Names: `feature/<slug>`, `fix/<slug>`, `chore/<slug>`. Slug is short,
  lowercase, hyphenated.
- **Push early:** `git push -u origin <branch>` right after the first commit. A
  pushed branch is the clearest signal the work is taken; check
  `git ls-remote --heads origin` before starting (the SessionStart hook prints it).
- **Never commit to `main`** and never merge into it. Integration is Don's step.

## Staying current: rebase, never merge

`git fetch origin && git rebase origin/main` on your branch. Never
`git merge main` into a feature branch and never a bare `git pull`. A merge
drags main's history into the branch and every later rebase pays for it. Set
`git config pull.rebase true`.

## Commits

- Imperative subject under 72 characters; body says why.
- Tests in the same commit as the change.
- Read `git diff --cached` before committing (`.claude/rules/secrets-and-public-repo.md`).

## Pull requests

**Open a PR only when Don asks for one in the current message.** Finishing a
task is a request to report the branch is pushed, not to open a PR. When asked,
check `gh pr list --state open` for a related branch first and ask whether to
combine.

## Banned for agents

No `--no-verify`, no force push (`--force`, `-f`, `--force-with-lease`), no
`git reset --hard`, no `git stash`, no `git clean -f`, no `git checkout -- .`
or `git restore .` over changes you didn't make. If `git status` shows a change
you didn't make, read the diff and leave it — it is probably someone else's
work. If a hook blocks you, fix the cause.
