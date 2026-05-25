# Decision Inbox

The Decision Inbox is the **human-in-the-loop** channel. Anything the bot cannot decide on its own (Quadrant D — irreversible + critical) is routed here as a GitHub Issue with a structured ballot. The owner responds via reaction or comment; the bot reads the response on the next cron tick and resumes.

This document specifies the protocol for opening, polling, and resolving inbox issues.

## Why a GitHub Issue, not email / Slack / push notification

- **Persistence.** GitHub Issues are durable; they survive owner laptop reboots, session compacts, and bot restarts.
- **Asynchrony.** The owner answers when convenient; the bot keeps polling on its cron schedule. No real-time channel to keep open.
- **Auditability.** Every inbox entry, every response, every state change is in `git log` / Issue history. No "did I approve that or not?" ambiguity later.
- **Zero new infrastructure.** No webhook server, no notification service, no separate mobile app. The owner already has GitHub notifications configured for their account.

## The inbox repository

By default, the inbox lives in the **governance repository** (the one that holds canonical `docs/concepts/`). For solo operators that's usually the same repo as the bot. For larger setups it can be a separate dedicated repo: set `decision_inbox.repository` in `config/projects.yml` (schema-validated at load time).

All Decision Inbox Issues carry the label `decision:pending-owner` (open) or one of the resolution labels (closed; see "Resolution states" below).

## Issue body schema

When the bot opens a Decision Inbox issue, the body follows this exact schema:

```markdown
**Owner approval required (Quadrant D)** — irreversible + critical.

Respond with 👍 (option A / approve), 👎 (reject), `/approve [A|B|C]` / `/reject`,
or tick a checkbox below.

## Options

- [ ] Option A — proceed as recommended
- [ ] Option B — alternate (see PR description)
- [ ] Option C — defer / needs more info

- PR: `<owner>/<repo>#<number>` — head `<short-sha>`
- Classifier: Quadrant D
- Reasoning: <classifier output summary, one sentence>
- Opened at: <ISO-8601 timestamp>

<!-- decision-inbox-nonce: <random-uuid> -->
<!-- decision-inbox-head-sha: <full sha of PR head at issue open> -->
```

The HTML-comment nonce and head-SHA are **invisible to humans** but read by the bot's polling logic for tamper detection. If the PR's head changes after the inbox issue opens (someone pushed new commits), the bot detects the mismatch and posts a "PR head changed — please re-confirm" comment instead of treating the old approval as valid.

## Polling logic

Every cron tick, `decision_inbox.py`:

1. Lists open Issues in `config/projects.yml` `decision_inbox.repository` (falls back to `governance_repo` if absent) with label `decision:pending-owner`.
2. For each Issue:
   a. Read reactions on the Issue body. Count only reactions by users in `config/owner.yml` `allowlisted_actors`.
   b. Read comments on the Issue, oldest-to-newest. Look for `/approve A`, `/approve B`, `/approve C`, or `/reject` commands from allowlisted actors.
   c. Read checkbox state on the Issue body. Checkbox edits by allowlisted actors count as ballot votes.
   d. If multiple signals exist, take the **most recent**.
3. If a verdict is found:
   a. Verify the PR head SHA still matches `decision-inbox-head-sha`. If mismatch → post "head changed" comment, do not resolve.
   b. Apply the verdict:
      - 👍 / `/approve A` → label PR `decision:approved-A`, return to L1 (C3 now passes).
      - Option B / `/approve B` → label PR `decision:approved-B`, comment indicates the alternate was chosen. Owner is expected to update the PR description with the actual alternate; L1 still requires CI green.
      - Option C / `/approve C` → label Issue `decision:deferred`, leave open. Owner may flip later.
      - 👎 / `/reject` → close PR with comment, close Issue with label `decision:rejected`.
4. Update tick metrics: `inbox_open_count`, `inbox_resolved_this_tick`, `inbox_oldest_age_hours`.

## Resolution states

When closed, an Issue has exactly one of these labels (in addition to `decision:pending-owner` which gets removed):

| Label                                       | Meaning                                       |
|---------------------------------------------|-----------------------------------------------|
| `decision:approved-A`                       | Owner approved option A — bot merged the PR.  |
| `decision:approved-B`                       | Owner approved option B (alternate).          |
| `decision:approved-C` + `decision:deferred` | Owner deferred; Issue stays open until they flip. |
| `decision:rejected`                         | Owner rejected; PR closed.                    |
| `decision:abandoned`                        | 30+ days no activity; auto-closed by inbox.   |
| `decision:auto-resolved-pr-closed`          | PR closed by author or CI; inbox auto-closes. |

## Allowlist enforcement

Only reactions/comments by users in `config/owner.yml` `allowlisted_actors` count. This prevents:

- An agent commenting `/approve A` on its own PR's inbox issue (the agent's bot login is not in the allowlist).
- A spam account creating accounts and 👍-bombing inbox issues.
- A previously-trusted teammate whose account was compromised post-hoc (the allowlist is checked at each tick, not at issue-open time).

The allowlist is `config/owner.yml` `allowlisted_actors` — typically `[<owner-github-login>]` for solo operators, plus optional delegated reviewers.

## Rate limiting & nudges

Inbox issues are designed for **asynchronous** response, not real-time. The bot does not page the owner. However:

- After **14 days** of no activity on an open issue, the bot posts a nudge comment ("Reminder: this Decision Inbox issue has been open for 14 days").
- After **30 days**, the bot labels `decision:abandoned`. The Issue stays open (no auto-close), but `decision:abandoned` signals that the linked PR is effectively dead until human action.
- After **60 days**, the bot closes the linked PR with `decision:auto-resolved-pr-closed` and closes the Issue. The owner can reopen if they want to revive.

These thresholds are configurable in `config/projects.yml` under `decision_inbox.thresholds` (keys: `nudge_days`, `abandon_days`, `auto_close_days`). Defaults: 14 / 30 / 60.

## Failure modes

### Owner reaction by mistake

Owner accidentally 👍-clicks. They can:

1. Remove the reaction within the same cron tick (5 minutes) — bot will not have polled yet.
2. Comment `/reject` after the 👍 — bot takes most recent signal.
3. Comment `/approve C` to defer — converts the approval into a hold.

### PR head changes after inbox opens

A new commit lands on the PR (e.g., the author pushed a fix). The inbox issue's `decision-inbox-head-sha` no longer matches. On the next tick, the bot:

1. Posts a comment on the inbox issue: "PR head changed from `<old>` to `<new>`. Please re-confirm your verdict if applicable."
2. Does **not** treat any prior reaction as valid. The owner must react/comment again after seeing the comment.

### Bot itself produces a Quadrant D PR

The bot does not gate its own PRs (chicken-and-egg — see [`break-glass.md`](break-glass.md) § "Bot self-update flow"). Bot-repo PRs use the break-glass flow (`[break-glass-bot-self-update]` commit prefix + ADR within 24h), not the Decision Inbox.

### Inbox issue accidentally closed by owner

Owner closes the issue with the GitHub UI without leaving a `/approve` or `/reject` comment. The bot treats this as `decision:auto-resolved-pr-closed` and closes the PR. If this was an accident, owner can reopen both issue and PR; the bot will re-evaluate from L1.

## Metrics

Every cron tick records:

```json
{
  "inbox_open_count": <int>,
  "inbox_resolved_this_tick": {
    "approved-A": <int>,
    "approved-B": <int>,
    "approved-C-deferred": <int>,
    "rejected": <int>,
    "abandoned": <int>
  },
  "inbox_oldest_age_hours": <float>,
  "inbox_head_sha_mismatches": <int>
}
```

A healthy inbox has `inbox_open_count` < 10 and `inbox_oldest_age_hours` < 168 (one week). Sustained higher numbers indicate either an owner who is over-loaded or a classifier that is over-quadrant-D'ing.
