# Don's voice

STATUS: TEMPLATE (not filled in yet)

> **Don:** fill in the sections below, then delete the `STATUS: TEMPLATE` line above.
> While that line is there, the `reply-voice` skill tells you so in every draft and
> falls back to **short, direct, plain**.
>
> This repo is **public**. Write your *style*, never content: no real names other than
> your own, no customers, no projects, no message text you've actually sent. For the
> examples, use made-up people (Alice Example, Bob Example) and a made-up company (ACME).

Used by `.claude/skills/reply-voice/SKILL.md`, the `reply-drafter` and `day-cycle`
agents, and the rubric in `evals/replies/rubric.md` (item 3).

**Phrases that must never appear** are not kept here. They are enforced by
`bin/check-draft` from
[`tools/messaging/src/wall3_messaging/draft_rules.toml`](../../tools/messaging/src/wall3_messaging/draft_rules.toml)
(`[banned] phrases`). To ban a phrase, add it there and it fails every draft. The
"avoid" list below is softer: things the drafter should steer away from, which the lint
does not block.

---

## Tone

_How you come across. One or two lines each._

- Default register: {{e.g. "friendly but brief; no small talk in work channels"}}
- With teammates: {{...}}
- With customers / people outside the team: {{...}}
- Humor: {{e.g. "rarely, and never when someone is upset"}}
- Emoji: {{e.g. "only 👍 / ✅ as acknowledgements; none in ClickUp"}}

## Length defaults

| Where | Default length | Notes |
|---|---|---|
| Slack DM | {{e.g. 1–2 sentences}} | |
| Slack channel / thread | {{e.g. 1–3 sentences}} | {{e.g. "bullets only for 3+ items"}} |
| ClickUp task comment | {{e.g. short paragraph; what changed + evidence}} | |
| ClickUp status-change comment | {{e.g. one line: why the status is moving}} | Goes with `bin/clickup status --comment-file` |

## Greetings and sign-offs

- Slack greeting: {{e.g. "none; start with the answer" / "Hey {name},"}}
- ClickUp greeting: {{...}}
- Sign-off: {{e.g. "none" / "-D"}}
- Uses the person's name: {{e.g. "only when the thread has several people"}}

## Phrases you use

_Things that sound like you. The drafter may use them, but not in every message._

- {{e.g. "Works for me."}}
- {{e.g. "Good catch."}}
- {{...}}

## Phrases you avoid

_Soft avoid list, not enforced by the lint. Hard bans go in `draft_rules.toml`._

- {{e.g. "Just circling back"}}
- {{e.g. "Per my last message"}}
- {{...}}

## How you say no

{{e.g. "Directly, with the reason in one clause, and an alternative if there is one.
Never 'unfortunately'."}}

## How you defer

_When you can't answer yet._

{{e.g. "Say what I'm waiting on and when I'll know. Don't give a date I haven't
checked."}}

## Escalation style

_When someone is upset, or something is broken for a customer._

{{e.g. "Acknowledge first, no defending. Say what's being done and who has it. Move
to a call if it's going back and forth."}}

## Things you never do in a reply

- {{e.g. "Promise a delivery date in a channel before it's on the board"}}
- {{e.g. "Tag @channel"}}
- {{...}}

---

## Examples

_Two or three short pairs per section, using synthetic names. The drafter learns
more from these than from the rules above._

### A quick yes

> **Them:** {{e.g. "Alice Example: OK if I merge the README PR?"}}
> **You:** {{your reply}}

### A no

> **Them:** {{...}}
> **You:** {{...}}

### A deferral

> **Them:** {{...}}
> **You:** {{...}}

### An upset customer

> **Them:** {{...}}
> **You:** {{...}}

### A ClickUp status-change comment

> **Task:** {{e.g. "ACME onboarding checklist"}}
> **You:** {{...}}
