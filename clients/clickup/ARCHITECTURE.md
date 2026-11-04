# wall3_clickup: architecture

## Shape

```
ClickUp REST (v2 + v3 Chat)
      │  client.py      retries, auth errors, pagination, status guard
      ▼
extract/            network only: writes every response verbatim
      │
      ▼
.cache/raw/clickup/<UTC ts>/     immutable snapshot (+ manifest.json)
      │
      ▼
normalize/          offline: raw dir -> Message objects (contract)
      │
      ▼
cli.py              human output or JSONL (--json)
```

- **Two phases, enforced.** `normalize/` never imports `requests`, `urllib`,
  `http`, `socket`, `dotenv`, the client, `extract/`, `config` or `cli`.
  `tests/test_normalize_boundary.py` parses the import statements (relative
  imports resolved) and fails the build on a violation. Normalization can be
  tuned and re-run against a fixed snapshot with no network access and no token.
- **Raw is verbatim.** Every file under a snapshot except `manifest.json` is
  exactly what an endpoint returned. `manifest.json` records the verb (`kind`),
  team id, `since_ms` window and `notes` (caps hit, chat skipped).
- **Snapshot layout:**
  `whoami.json`, `teams.json`, `task_pages/page-NNN.json`, `tasks/<id>.json`,
  `comments/<task_id>/page-NNN.json`, `replies/<comment_id>.json`,
  `chat/channels/page-NNN.json`, `chat/channel/<id>.json`,
  `chat/messages/<channel_id>/page-NNN.json`, `chat/replies/<message_id>/page-NNN.json`,
  `chat/tagged_users/<message_id>.json`. API ids become path components only when
  they match `[A-Za-z0-9_.:-]+`. Anything else is refused, never rewritten,
  because normalize reads ids back from directory names.

## Decisions

- **Auth:** personal token in `Authorization` (no OAuth; one user, own workspace).
- **Config:** worktree-aware `.env` search (package → working root → primary
  checkout via `git rev-parse --path-format=absolute --git-common-dir`),
  `override=False`, plus a missing-token error that lists every searched path
  as found or not found. The token field has `repr=False`.
- **Retries:** 429 → sleep until `X-RateLimit-Reset` (Unix seconds), else
  `Retry-After`, else 10 s, capped at 65 s, for every method. 5xx and
  connection errors → `min(2^n, 30)` s backoff, **only for GET/PUT/DELETE**:
  a POST may have succeeded server-side, and retrying a comment could
  double-post to a real person. 401/403 → `ClickUpAuthError`, never retried.
  There is no sleep after the final attempt. `session`, `sleep` and `clock`
  are injectable.
- **SilentStatusChange:** `update_task_status` refuses (before any request)
  unless this client instance has commented on that task.
  `allow_silent_status=True` is the explicit escape. The CLI never uses it.
- **Write verbs:** preview without `--confirm` (exit 3, zero network calls);
  text only from files.
- **Message semantics** (also in `normalize/messages.py`):
  - *thread*: task comments share `thread_id` = task id, including threaded
    replies, as the contract specifies. In chat, a reply and its root share
    the root's id. A top-level message with no replies has `thread_id: null`,
    and for `awaiting_reply` its thread is the channel's top-level stream.
  - *is_mention*: someone other than me tagged me. That means a task-comment
    tag item `{"type":"tag","user":{"id":<me>}}`, my id in a chat message's
    tagged users, or `@<my full name>` in the text (case-insensitive). A bare
    first name does not count. A message I wrote is never a mention.
  - *is_dm*: the chat channel type is `DM` or `GROUP_DM`. Container kinds are
    `CHANNEL`→`chat`, `DM`→`dm`, `GROUP_DM`→`mpim`. Task comments → `task`.
  - *awaiting_reply*: not mine, mentions or DMs me, and the latest message in
    its thread is not mine. Ties count as answered. Messages older than the
    window still count when deciding which message is latest.
  - *inbox relevance*: inbox output keeps only messages inside the window that
    concern me: comments on tasks I'm assigned to, watch or created, or whose
    thread mentions me; chat DMs, chat mentions, and reply threads containing
    a mention. `task` and `chat messages` snapshots are emitted whole.
  - Permalinks: task `https://app.clickup.com/t/<task_id>` (also the task's
    `url` field), comment `https://app.clickup.com/t/<task_id>?comment=<id>`,
    chat `https://app.clickup.com/<workspace_id>/chat/r/<channel_id>`
    (approximation, see Known limits).
- **Output order:** Messages sorted by `(ts, id)`.

## Verified endpoints

Each one was checked against the OpenAPI definition embedded in its reference
page (fetched 2026-11-04): paths, parameter names, and response fields used.

| Use | Endpoint | Doc |
|---|---|---|
| whoami | `GET /api/v2/user` → `user.id` (int), `user.username` | https://developer.clickup.com/reference/getauthorizeduser |
| workspaces + member names | `GET /api/v2/team` → `teams[].id/name/members[].user{id,username}` | https://developer.clickup.com/reference/getauthorizedteams |
| task | `GET /api/v2/task/{task_id}` → `url`, `name`, `status.status`, `assignees`, `watchers`, `creator` | https://developer.clickup.com/reference/gettask |
| comments | `GET /api/v2/task/{task_id}/comment`; newest first, 25 per page; next page via `start` (= last comment's `date`, ms) + `start_id` | https://developer.clickup.com/reference/gettaskcomments, https://developer.clickup.com/docs/task-comments-pagination |
| comment structure | `comment[]` items; a tag is `{"type":"tag","user":{"id":...}}`; also `comment_text`, `user`, `date` (ms string), `reply_count` (string) | https://developer.clickup.com/docs/comment-formatting |
| threaded replies | `GET /api/v2/comment/{comment_id}/reply` (parent not included) | https://developer.clickup.com/reference/getthreadedcomments |
| task filter | `GET /api/v2/team/{team_id}/task`: `page` (from 0, 100 per page), `order_by`, `assignees[]`, `statuses[]`, `include_closed`, `subtasks`, `date_updated_gt` (ms) | https://developer.clickup.com/reference/getfilteredteamtasks |
| post comment | `POST /api/v2/task/{task_id}/comment` `{comment_text, notify_all}` (both required) → `{id, hist_id, date}` | https://developer.clickup.com/reference/createtaskcomment |
| set status | `PUT /api/v2/task/{task_id}` `{status}` | https://developer.clickup.com/reference/updatetask |
| chat channels | `GET /api/v3/workspaces/{workspace_id}/chat/channels`: `cursor`, `limit` (1-100), `with_message_since`; → `next_cursor`, `data[]{id,name,type,workspace_id,...}` | https://developer.clickup.com/reference/getchatchannels |
| one channel | `GET /api/v3/workspaces/{workspace_id}/chat/channels/{channel_id}` → `{data: channel}` | https://developer.clickup.com/reference/getchatchannel |
| channel messages | `GET /api/v3/workspaces/{workspace_id}/chat/channels/{channel_id}/messages`: `cursor`, `limit`, `content_format` (`text/md` default or `text/plain`); → `data[]{id,user_id,content,date(ms),parent_channel,parent_message?,replies_count}` | https://developer.clickup.com/reference/getchatmessages |
| message replies | `GET /api/v3/workspaces/{workspace_id}/chat/messages/{message_id}/replies` | https://developer.clickup.com/reference/getchatmessagereplies |
| tagged users | `GET /api/v3/workspaces/{workspace_id}/chat/messages/{message_id}/tagged_users` → `data[]{id,name,username,email}` | https://developer.clickup.com/reference/getchatmessagetaggedusers |
| rate limits | 100 req/min per token (Free/Unlimited/Business); 429 + `X-RateLimit-Reset` (Unix ts) | https://developer.clickup.com/docs/rate-limits |
| auth | `Authorization: pk_...` | https://developer.clickup.com/docs/authentication |

## Known limits

1. **No inbox, notifications, mentions or watched-tasks endpoint** exists in
   the public API (checked against https://developer.clickup.com/llms.txt).
   `inbox` approximates one. It reads workspace tasks with
   `date_updated_gt = since` (closed tasks and subtasks included; at most
   5 pages = 500 tasks), sorts them newest first, and reads comments on at most
   **100** of them, at least one request each. Then it reads chat channels with a message since the window
   start (at most **50**). Hitting either cap writes a note to the manifest
   and to stderr.
   - **Unverified assumption:** a new comment bumps the task's `date_updated`.
     The docs don't say. If it doesn't, a mention on a task that was otherwise
     untouched in the window will be missed.
   - Watched-task relevance comes from the `watchers` field on returned tasks;
     there is no "tasks I watch" filter.
2. **No task text search.** `search` reads up to 500 tasks (closed included)
   requested with `order_by=updated`. The docs don't give the sort direction,
   so in a large workspace these may not be the 500 *most recent*. Every query
   term must appear in the name, custom id or description (case-insensitive).
   It says so on stderr.
3. **Chat API is experimental** ("subject to change", per
   https://developer.clickup.com/docs/chat). If a chat call fails during
   `inbox`, the task half still runs and the manifest/stderr says `chat skipped`.
   - **No chat permalink** is returned by the API (`links` are API resource
     paths). The CLI uses `https://app.clickup.com/<workspace_id>/chat/r/<channel_id>`,
     which is **unverified** and points at the channel, not the message.
   - **Message order is not documented.** Paging stops once a page reaches
     messages older than the window, which assumes newest-first order (as task
     comments are). If the order is the reverse, `inbox` could stop early. Pages
     are capped either way (3 × 50 per channel in `inbox`, 2 × 50 in
     `chat messages`).
   - **How mentions render in `text/plain` is not documented.** Tagged users
     cost one request per message, so they are fetched only for messages that
     I didn't write, that fall inside the window, and whose content contains
     `@`. A mention rendered without `@` would be missed unless the text
     contains `@<my full name>`.
   - Chat messages carry only `user_id`. Names come from `teams.json` members
     and tagged-user lookups. An unknown user shows as their id.
   - DM channels often have an empty `name`. The container name becomes the
     other authors seen in the snapshot, falling back to the channel id.
4. **Task comments in `inbox`** are read up to 4 pages (100 comments) per task,
   stopping once a page reaches comments older than the window. Replies are
   read for any comment with `reply_count > 0`, one request each.
5. **Threaded replies share the task's `thread_id`** (per the contract). So my
   reply anywhere on a task counts as answering every earlier mention on that
   task. That errs toward `awaiting_reply: false`.
6. A comment or reply whose `user` is missing gets author id `"unknown"`. A
   reply file whose parent comment is not in the snapshot is skipped with a
   warning, because its task can't be determined.
