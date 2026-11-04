# wall3_slack: Architecture

**Status:** implemented, offline-tested; not yet run against a live workspace (no token exists yet).
**Scope:** `clients/slack/`, the `slack` CLI behind `bin/slack`.

## Purpose

This package lets the harness triage Don's Slack and draft replies through the
Web API directly. It replaces the MCP connector, which is rate-limited. Claude
drafts and Don approves every send. The client authenticates with a **user
token**, so it reads what Don can read: his DMs, his mentions, and
`search.messages`, which needs a user token. Anything it posts is posted as Don.

## Decisions

### Two phases: extract, then normalize

```
Slack Web API --extract/--> .cache/raw/slack/<UTC ts>/ --normalize/--> Message JSONL
                            (verbatim responses)        (offline, re-runnable)
```

* `extract/` makes network calls and writes every response to disk verbatim,
  plus a `manifest.json` run record: verb, window, caps, call counts, and
  what was truncated or skipped.
* `normalize/` reads only the raw directory. `tests/test_normalize_boundary.py`
  parses the AST of every module under `normalize/` and fails the build if
  any of them imports `requests`, `urllib3`, `http.client`, `socket`,
  `wall3_slack.client` or `wall3_slack.extract`. This includes relative
  imports like `from ..client import`. A paired test plants each of those
  import shapes and confirms the check catches them.
* Payoff: changing how messages are rendered costs no API calls. You re-run
  `slack normalize <dir>`. `scripts/check_message_contract.py` runs that same
  verb against the committed fixture.

### `requests`, not `slack_sdk`

The surface is small: one auth header, about ten methods, one retry policy.
The SDK would hide the retry and redaction behavior that the tests pin. The
HTTP session and `sleep` are injected, so every test runs offline.

### Retry policy

| Condition | Reads | Writes (`chat.postMessage`) |
|---|---|---|
| HTTP 429, or `ok:false error:ratelimited` | sleep `Retry-After` (else 1, 2, 4... s), retry up to 5 times | same: the request was rejected, not processed |
| `Retry-After` > 120 s | raise `SlackRateLimitError` | same |
| HTTP 5xx or transport error/timeout | exponential backoff, retry | **never retried.** The post may have landed, and a retry would duplicate it |
| `invalid_auth`, `not_authed`, `token_revoked`, `token_expired`, `account_inactive`, `missing_scope`, `not_allowed_token_type`, `no_permission`, HTTP 401/403 | `SlackAuthError`, never retried | same |
| Any other `ok:false` | `SlackAPIError` carrying `.error` | same |

`missing_scope` names the scope to add. It uses Slack's `needed` field when
present, otherwise a method-to-scope table.

### Configuration and the token

`config.py` ports the worktree-aware lookup. It searches `clients/slack/.env`,
then the working repo root's `.env`, then the primary checkout's `.env`
(found with `git rev-parse --path-format=absolute --git-common-dir`). It loads
with `override=False`, so an exported variable wins. If no token is found, the
error lists every path searched and says whether it is running in a worktree.
The tests build real git repositories and worktrees in `tmp_path`. The token
is a `repr=False` dataclass field, `SlackClient.__repr__` redacts it, and
errors never include it.

### Permalinks are constructed, not fetched

Permalinks are built as `auth.test.url + "archives/<C>/p<ts without dot>"`.
Replies append `?thread_ts=<parent>&cid=<C>`. This is the format
`chat.getPermalink` documents. A `chat.getPermalink` call per message would
cost one request per line of output. Search matches come with Slack's own
`permalink`, and normalize keeps it. `send --confirm` does call
`chat.getPermalink` once, for the message it just posted.

### `awaiting_reply`, read literally

From the contract: true when "last message in its thread is not from me AND
it DMs or mentions me". In this implementation:

* **Its thread:** a message's thread is `thread_ts` if it has one. Otherwise
  it is its conversation's top-level stream. "Last" is decided across every
  message in the raw dir, including context messages older than the window.
  For example, a thread parent fetched by `conversations.replies`.
* **DMs me:** the message is in a `dm` or `mpim` and is not mine.
  **Mentions me:** its raw text contains `<@MY_ID>`. My own messages are never
  awaiting a reply. `@here` and `@channel` are not mentions.
* **Consequence:** under this reading, every non-me message in a DM stream
  where the other person spoke last is `awaiting_reply: true`, not only the
  newest one. A reply from someone else in a thread I started, with no
  mention of me, is `false`.
* `is_dm` is true for both `dm` and `mpim`.
* Dropped: system subtypes such as joins, topic changes and pins. Only `None`,
  `bot_message`, `thread_broadcast`, `file_share` and `me_message` are kept.
  A bot's author is `{id: bot_id, name: bot_profile.name or username}`.

### Text rendering

Text rendering follows Slack's documented mrkdwn escaping and reference
syntax:

* `<@U…>` becomes `@Display Name`. It falls back to the label in the token,
  then to the raw id.
* `<#C…|x>` becomes `#x`.
* `<!here>` becomes `@here`.
* `<!subteam^…|@g>` becomes `@g`.
* `<!date^…|fallback>` becomes `fallback`.
* `<url|label>` becomes `label (url)`, and `<url>` becomes `url`.
* `<mailto:…|x>` becomes `x`.
* `&lt; &gt; &amp;` are unescaped, with `&amp;` last.

## Verified API facts

Verified 2026-11-04 against docs.slack.dev. "Tier" is per method, per
workspace, per app.

| Method | Tier | HTTP | User scope(s) | Doc |
|---|---|---|---|---|
| `auth.test` | special ("hundreds per minute") | POST | none | https://docs.slack.dev/reference/methods/auth.test |
| `conversations.list` | Tier 2 (20+/min) | GET | `channels:read groups:read im:read mpim:read` | https://docs.slack.dev/reference/methods/conversations.list |
| `conversations.info` | Tier 3 (50+/min) | GET | same four `:read` | https://docs.slack.dev/reference/methods/conversations.info |
| `conversations.history` | Tier 3 (50+/min)* | GET | `channels:history groups:history im:history mpim:history` | https://docs.slack.dev/reference/methods/conversations.history |
| `conversations.replies` | Tier 3 (50+/min)* | GET | same four `:history` | https://docs.slack.dev/reference/methods/conversations.replies |
| `users.info` | Tier 4 (100+/min) | GET | `users:read` | https://docs.slack.dev/reference/methods/users.info |
| `users.list` | Tier 2 (20+/min) | GET | `users:read` | https://docs.slack.dev/reference/methods/users.list |
| `users.conversations` | Tier 3 (50+/min) | GET | same four `:read` | https://docs.slack.dev/reference/methods/users.conversations |
| `search.messages` | Tier 2 (20+/min) | GET | `search:read` (**user token only**) | https://docs.slack.dev/reference/methods/search.messages |
| `chat.postMessage` | special (~1 msg/s per channel) | POST, JSON body | `chat:write` | https://docs.slack.dev/reference/methods/chat.postMessage |
| `chat.getPermalink` | special | GET | none | https://docs.slack.dev/reference/methods/chat.getPermalink |

\* See the 2025 change below.

General rules:

* **Auth.** The token must be sent as `Authorization: Bearer`, never in the
  query string or a JSON body. Write methods accept JSON with an explicit
  `Content-Type: application/json`. Every response has a boolean `ok`, and
  failures carry an `error` code. Source: https://docs.slack.dev/apis/web-api/
* **429 behavior.** Slack returns HTTP 429 with `Retry-After` in seconds. Only
  that method, for that workspace, is limited. Source:
  https://docs.slack.dev/apis/web-api/rate-limits
* **Cursor pagination.** Pass `cursor` and read
  `response_metadata.next_cursor`. Docs recommend at most 200 per page.
  `conversations.list` may return short pages because filters are applied
  after the page is fetched.
* **`search.messages` pagination.** It uses `page`/`count` (max 100 per page)
  with `messages.paging.pages`. The docs mark the method as **legacy**, with
  the Real-time Search API (`assistant.search.context`) as its successor.
  It remains documented and supported for user tokens.
* **Search modifiers.** Slack's search docs state that `before:/after:/on:`
  take `YYYY-MM-DD`, and that IDs in filters are written `<@U…>`. Source:
  https://docs.slack.dev/reference/methods/assistant.search.context. Search
  matches carry `channel{id,name,is_mpim,...}`, `user`, `username`, `ts`,
  `text` and `permalink`, but **no `thread_ts`**. Replies are identified from
  the permalink's `?thread_ts=` query.
* **Manifest scopes** (`oauth_config.scopes.user`). All eleven scope names
  exist (https://docs.slack.dev/reference/scopes), and each method page above
  lists them under "User token". Manifest format:
  https://docs.slack.dev/reference/app-manifest

### The 2025 rate-limit change, and why it does not block this design

Source: https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps

This change applies to **commercially distributed apps not approved for the
Slack Marketplace**. For those apps, `conversations.history` and
`conversations.replies` drop to **1 request/minute**, and `limit` is capped at
**15** messages. It applies immediately to new apps and new installs as of
2025-05-29.

The changelog says: "Internal customer-built applications are not impacted by
these changes." Custom apps keep 50+ requests/minute and a `limit` of up to
1,000.

Our app is created from `slack-app-manifest.yaml` in Don's own workspace,
installed only there, and **never distributed**. That makes it an internal
customer-built app, so it is exempt. The docs do not formally define
"internal", so treat this as the documented reading, not a guarantee.

* **How you would know it changed:** history pages that return at most 15
  messages, and 429s on the second history call within a minute. If that
  happens, `inbox` stays correct but slow, with every DM's history call
  serialized at one per minute. The fallback design: drop per-DM history,
  build the inbox from `search.messages` (Tier 2, unaffected), and fetch
  `conversations.replies` only for threads that are awaiting a reply.

## Inbox strategy and its limits

Defaults are in `extract/inbox.py::InboxLimits`.

| Step | Calls | Cap (default) | Limit |
|---|---|---|---|
| `auth.test` → `whoami.json` | 1 | none | none |
| `conversations.list types=im,mpim` | ≤ `max_list_pages` (10) × 200 | 2,000 DMs | DMs past the cap are not listed; reported |
| `conversations.history oldest=<since>` per DM | ≤ `max_conversations` (50) × `max_history_pages` (3) | 50 DMs, 600 msgs each | Slack gives no reliable "last activity" on conversation objects, so DMs are ordered by `updated`, then `created`, as a heuristic. A quiet DM past the cap is skipped and reported. `conversations.info` does return `latest`, but costs one Tier-3 call per DM, the same as reading history. |
| `search.messages "<@ME> after:<oldest-2d>"` | ≤ `max_search_pages` (3) × 100 | 300 mentions | `after:` is day-granular and timezone-dependent, so the date is padded back 2 days and normalize cuts exactly at `oldest`. Paging stops once results predate the window. |
| `search.messages "from:<@ME> after:<oldest-7d-1d>"` | ≤ 3 × 100 | 300 of my messages | Used for **discovery only** (`manifest.discovery_only`), never emitted. A thread I last posted in more than `thread_lookback_days` (7) ago is not found. |
| `conversations.replies` per thread | ≤ `max_threads` (40) | 40 threads | Order: mention threads, then DM threads whose `latest_reply` falls in the window, then threads I posted in. My top-level messages with no replies still cost a call each, because search matches carry no `reply_count`. Fetched without `oldest`, so the parent and full context are present. |
| `users.info` per referenced user | ≤ `max_users` (200) | 200 | Cached in-process. Unresolved users render as their raw id. |

Worst case with defaults: about 1 + 10 + 150 (Tier 3) + 6 (Tier 2) + 40
(Tier 3) + 200 (Tier 4) calls. Each tier is counted separately, so the run
fits in about 3–4 minutes even if it hits 429s, and finishes in seconds on a
typical day. Every cap that is hit is recorded in `manifest.json.truncated`
and echoed to stderr. Nothing is silently dropped.

Not covered:

* Threads I follow but never posted in.
* Reactions.
* Edits and deletes after extraction.
* Messages in channels I left.
* `@here`/`@channel` and user-group mentions. These are not counted as
  mentions and not searched for.

## Unverified until the first live run

These assumptions cannot be checked without a token:

* `search.messages` accepts `<@U…>` and `from:<@U…>` with IDs in the
  legacy method's query. This is documented for the Real-time Search API.
  If it fails, fall back to `@handle` and `from:@handle` from `auth.test.user`.
* The `name` that search returns for an IM channel is the other user's ID.
  This is only used as a fallback for the container name.
* `updated` on IM objects. It is absent in the docs' IM example, so the
  ordering falls back to `created`.
