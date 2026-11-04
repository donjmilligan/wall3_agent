---
description: Core working rules — read before claiming, scope discipline, verification before DONE.
alwaysApply: true
---

# Core rules

## Investigation and accuracy

- Never speculate about code you have not read. Read the file and grep for
  usages before making a claim about it.
- If Don references a file, a thread or a task, read it before answering.
- If you are unsure, say so and say how you would verify it. Never invent an
  API, a flag, a path or a behaviour.
- A fact from memory, a summary or an earlier session is a pointer that may be
  stale. Confirm it against the live file or command before acting on it.

## Contract first

- The Message contract is `docs/contracts/message.schema.json`. Both CLIs emit
  it; `tools/messaging/` consumes it. Never hand-roll a shape that diverges.
- Changing the contract means updating the schema, both producers and every
  consumer in the same change, and re-running
  `scripts/check_message_contract.py`.

## Scope discipline

- Do what was asked; nothing more, nothing less. Follow "only", "just" and
  "exactly" literally.
- When intent is ambiguous, research and recommend; edit only when asked.
- No refactors of adjacent code, no docstrings on unchanged code, no
  abstraction for a single use. Note "while I'm here" findings separately.
- Stay inside the paths your task owns. Crossing into another package is a
  stated, deliberate act, never a side effect.

## Verification before DONE

- Re-read the request, run the gate for every package the diff touches
  (`.github/workflows/ci.yml` is the authoritative list), and state what
  changed and what you could not verify.
- Then run the concerns pass (`.claude/rules/completion-protocol.md`).
- Ask before destructive actions: deleting files or branches, force pushes,
  hard resets, `--no-verify`. Most are denied outright in
  `.claude/settings.json`.

## Efficiency

- Parallelise independent tool calls; serialise dependent ones.
- Never use placeholder or guessed parameters — look the value up.
- Edit existing files in place; create new ones only when required.
