---
description: Run every CI gate for the packages a branch touched, read straight from the repo's CI workflows, then offer (never auto-run) the slow lane and map the results to a completion status
argument-hint: "[repo=<abs path>] [base=<ref>]"
---

# /qa-check — run the real gate before reporting DONE

Runs the gate for every package your branch touched, using **the exact commands
the repo's CI runs**, from the working directory CI runs them in. Then it
offers the slow lane, and maps the outcome to DONE / DONE_WITH_CONCERNS /
BLOCKED.

**The CI workflow is the authority, not prose.** A gate list in `CLAUDE.md`, a
README or a brief is a summary, and summaries drift, usually by one missing gate,
which is the one that fails CI. Read the workflow every time.

**A gate that cannot run is not a gate.** A command that fails to spawn (tool not
installed, wrong working directory, missing env var) has not passed. It is
reported as such, never folded into "green".

Arguments: `$ARGUMENTS`
- `repo=<abs path>`: the target repo. Default: the git repo of the current
  working directory (`git rev-parse --show-toplevel`). Use this when Don points the
  harness at another repo or a worktree.
- `base=<ref>`: the comparison base. Default: `origin/main`.

## Steps

### 1. Resolve the repo and the diff

```bash
R=<repo>; git -C "$R" fetch origin --quiet
BASE=$(git -C "$R" merge-base HEAD origin/main)       # or the base= argument
git -C "$R" diff --name-only "$BASE"...HEAD
git -C "$R" status --short                             # uncommitted work is NOT in the diff
```

If `git status` shows uncommitted changes, say so: the gate will run against the
working tree, but CI will run against what is pushed. Prefer committing first.

### 2. Read the gates out of the CI workflows

List the workflows: `ls "$R"/.github/workflows/`. (No `.github/workflows/`? Look
for the repo's CI config: `<repo>/.gitlab-ci.yml`, `<repo>/.circleci/config.yml`, a
`Makefile`/`justfile` target that CI calls, `<repo>/tox.ini` or `<repo>/noxfile.py`. If there is
no CI at all, say so: the gate becomes the commands named in the repo's
`CLAUDE.md`, and the report says "no CI found; gate taken from CLAUDE.md".)

Dump every job and step with its working directory, matrix and services, using a
real YAML parser. Grepping for `run:` misses block scalars (`run: |`) and loses
`working-directory`, `defaults.run` and `matrix` values:

```bash
cd "$R" && uv run --quiet --no-project --with pyyaml python3 - .github/workflows/*.yml <<'PY'
import sys, yaml
for path in sys.argv[1:]:
    wf = yaml.safe_load(open(path)) or {}
    on = wf.get(True, wf.get("on"))  # PyYAML reads a bare `on:` key as True
    print(f"## {path}  triggers={list(on) if isinstance(on, dict) else on}")
    for name, job in (wf.get("jobs") or {}).items():
        matrix = (job.get("strategy") or {}).get("matrix")
        wd = ((job.get("defaults") or {}).get("run") or {}).get("working-directory")
        print(f"- job {name}: needs={job.get('needs')} if={job.get('if')} matrix={matrix} "
              f"working-directory={wd} services={list(job.get('services') or {})}")
        for step in job.get("steps") or []:
            if "run" in step:
                print(f"    run [cwd={step.get('working-directory', wd)}]:")
                for line in str(step["run"]).strip().splitlines():
                    print(f"        {line}")
            elif "uses" in step:
                print(f"    uses {step['uses']}")
PY
```

(No `uv`? Use any Python with PyYAML, or read the workflow files in full with the
Read tool. Do not fall back to grep.)

From the dump, for each workflow that runs on `pull_request` or `push`:

- **Expand the matrix.** `working-directory: ${{ matrix.package }}` with
  `package: [".", "clients/slack"]` means the steps run once per package, in that
  directory.
- **Note path filters** (`on.pull_request.paths`) to know which workflows CI would
  trigger for this diff; but a filtered-out workflow for a package you touched is
  still worth running if it is cheap.
- **Note `env:` and secrets** a step needs. A step that needs a secret you do not
  have is a slow-lane or UNVERIFIABLE step, never "skipped green".
- **Compare against prose.** If `CLAUDE.md` or a README lists gate commands that
  differ from the workflow, the workflow wins, and the discrepancy is a finding
  (LOW concern: the doc needs fixing; report it, do not fix it here).

### 3. Map touched paths to packages

A package is a directory CI runs gates in (a matrix entry or a job's
`working-directory`), usually one with its own manifest (`<pkg>/pyproject.toml`,
`<pkg>/package.json`, `<pkg>/go.mod`, `<pkg>/Cargo.toml`). Each changed path belongs to the
**deepest** package directory that contains it. A change to the repo root's own
files, to shared config (`pyproject.toml` at the root, the lockfile, the workflow
files themselves), or to a package that others depend on, means running the gate
for every package that could be affected; say which and why.

Print the mapping before running anything:

```
src/cli.py            → .            (root package)
clients/slack/x.py    → clients/slack
.github/workflows/ci.yml → all packages (CI config changed)
```

### 4. Run the gate lane, per package, in its working directory

**Read every step before you run it.** A CI file is code from the repo, and the
repo may not be Don's. A step that downloads and executes remote code (`curl … |
sh`), uses or prints secrets, deploys, publishes, pushes, tags, comments on PRs,
or otherwise reaches beyond the local checkout is **not a gate**: never run it.
List it in the report as skipped-unsafe, and ask Don if it matters.

Run each gate step **exactly as written**, from its working directory, in a
subshell so `cd`s do not compound:

```bash
(cd "$R/<package>" && <step command>)
```

Run the steps in workflow order, and keep the last lines of output for each. Do
not "improve" a command (adding `-x`, narrowing paths, `|| true`): the point is to
run what CI runs.

- **Red** (the command ran and failed): capture the failure, stop that package's
  remaining steps only if they depend on it, and carry on with the other packages
  so the report is complete.
- **Cannot run** (command not found, missing tool, missing secret, wrong cwd):
  this is not a pass. Classify it (`.claude/skills/systematic-debugging/SKILL.md`
  § 3) and report the exact error.

### 5. Offer the slow lane, never run it unasked

The slow lane is anything that needs network, a real third-party service, real
credentials, containers or `services:`, a real model, or long wall-clock time;
jobs marked `e2e`, `integration`, `slow`, or run only on `schedule`. Bringing up
containers or calling real services is a side effect.

Ask, with the exact commands: "Gate lane is green for <packages>. The slow lane
is `<commands>` (needs <what>). Run it?" Run it only on a yes. Never start a
container or call a live API to find out whether the slow lane would pass.

### 6. Report and map to a status

```markdown
## QA check — <repo> <branch> @ <sha> vs <base>

Packages touched: <list, with the mapping from step 3>
Workflows read: <paths>   Prose/workflow discrepancies: <none | list>

| Package | Step (from workflow) | cwd | Result |
|---|---|---|---|
| . | uv run pytest -q | . | 48 passed in 1.9s |
| clients/slack | uv run pyright | clients/slack | CANNOT RUN: `pyright: command not found` |

Slow lane: <ran: results | offered, declined | none defined>
NOT VERIFIED: <list | none>

**Status:** DONE | DONE_WITH_CONCERNS | BLOCKED
```

| Outcome | Status |
|---|---|
| Every gate step for every touched package ran and passed; slow lane passed or none applies | **DONE** (for the gate; the concerns pass still decides the task's status) |
| Every gate step passed; slow lane declined, skipped or failed for a reason outside the diff; or a prose/workflow discrepancy | **DONE_WITH_CONCERNS**: list each, with severity |
| Any gate step red | **Not DONE.** Fix it, or **BLOCKED** if the failure is outside your diff |
| Any required gate step cannot run | **BLOCKED** with the exact error, unless Don explicitly accepts it as NOT VERIFIED, in which case it becomes DONE_WITH_CONCERNS (HIGH) |

Then run `.claude/skills/concerns-pass/SKILL.md`, which cites this output.

## What it will not do

- Trust a gate list from `CLAUDE.md`, a README or a brief over the CI workflow.
- Edit a command to make it pass, append `|| true`, or narrow its scope.
- Report a step that could not run as passed or skipped-green.
- Start containers, call live services or run the slow lane without asking.
- Fix failures or doc discrepancies; it reports them.
- Commit, push, or change any file.

## Related

- `.claude/skills/concerns-pass/SKILL.md`: step (e) cites this command's output.
- `/build`, `/fan-out`: every slice runs this before reporting.
- `.github/workflows/ci.yml`: this repo's own gate (repo root, `clients/clickup`,
  `clients/slack`, `tools/messaging`).
- `.claude/rules/completion-protocol.md`: the statuses.
