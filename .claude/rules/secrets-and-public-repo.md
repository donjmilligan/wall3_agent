---
description: This repo is public — no tokens, .env, exports, caches, real IDs, emails or message content in git; fixtures are synthetic.
alwaysApply: true
---

# Secrets and the public repo

`github.com/donjmilligan/wall3_agent` is **public**. Everything committed is
published, forever — deleting it later does not unpublish it.

## Never commit

- **Tokens and keys** of any kind: Slack (`xox?-`, `xapp-`), ClickUp
  (`pk_<n>_…`), Anthropic (`sk-ant-`), GitHub (`ghp_`, `github_pat_`), and
  anything else credential-shaped.
- **`.env` and `.env.*`** — only `.env.example`, with empty values, is tracked.
- **Message data:** `.cache/`, `exports/`, raw API responses, pasted threads,
  screenshots of real conversations.
- **Real identifiers:** Slack channel/user/team IDs, ClickUp
  workspace/list/task/user IDs, permalinks to real messages.
- **Personal data:** names other than Don's, email addresses, phone numbers,
  hostnames, IP addresses.
- `.claude/settings.local.json` (per-developer, gitignored).

## Fixtures are synthetic

Test data is invented, not redacted: made-up names ("Alex Example"),
placeholder IDs (`C0TEST`, `U0TEST`), `example.com` / `example.invalid`
domains. Redacting a real export leaks structure and misses things. In test
code, assemble token-shaped strings at runtime (`"xo" + "xb-" + ...`) so the
source never contains one.

## Enforcement

- `.gitignore` excludes `.env*`, `.cache/`, `exports/`, `.claude/settings.local.json`.
- `.claude/hooks/pre_commit_guard.py` blocks a commit that stages any of those
  paths or token-shaped content. It is a backstop for the mechanical cases —
  it cannot recognise a real name, ID or message. You can.
- Before every commit, read `git diff --cached` for anything private.

## If something leaks

Stop. Tell Don immediately, with the commit and the file. **Rotate the
credential first** — a pushed token is compromised whatever happens to the
history. Don't force-push or rewrite history yourself; that is Don's call.
