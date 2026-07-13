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

## Optional lifecycle (default OFF)

The framework default remains fully asynchronous: with no lifecycle block, or
with `enabled: false`, the bot performs **no timer or availability API calls**
and posts no reminder, escalation, or return-digest comments. An installation
may deliberately opt into a notification-only lifecycle in
`config/projects.yml`:

```yaml
decision_inbox:
  repository: example-org/decision-inbox
  lifecycle:
    enabled: true
    reminder_hours: 72
    escalate_hours: 168
    availability:                   # optional
      repository: example-org/operations
      path: status/availability.md
      ref: main
      line_prefix: "[OWNER_AVAILABILITY]"
```

`escalate_hours` must be greater than `reminder_hours`. At the default
thresholds, the bot posts one `[REMINDER_72H]` comment at 72 effective hours
and one `[ESCALATION_7D]` comment at 168 effective hours. Stable hidden markers
make both comments idempotent across stateless cron restarts, even if thresholds
are later reconfigured. If a tick first sees an issue after both thresholds,
it posts each missing marker once. The lifecycle never approves, rejects,
labels an issue abandoned, or closes an issue or PR.

The optional availability file uses the configured literal `line_prefix`; no
repository or path is built into the framework. Valid contract lines are:

```text
[OWNER_AVAILABILITY] available
[OWNER_AVAILABILITY] quiet
[OWNER_AVAILABILITY] 2026-08-01T00:00Z - 2026-08-01T12:00Z outing
```

Hyphen, en-dash, and em-dash window separators are accepted. The last valid
line supplies the current state, while every valid `outing` window in the file
contributes to clock suspension. `quiet` suppresses lifecycle comments but does
not invent an unbounded pause; only an explicit outing window stops elapsed
time. For example, an issue at 60 effective hours followed by a 12-hour outing
is still at 60 effective hours at return and reaches the 72-hour reminder 12
hours later.

After an outing window ends, each still-open issue receives one
`[OWNER_RETURN_DIGEST]` comment. That marker records every completed window the
tick observed, so the paused-clock calculation and exactly-once behavior
survive a restart. If the same issue survives a later outing, the bot appends
that window's hidden metadata to the same digest comment instead of posting a
second digest. The source line may then be removed, but each completed outing
line must remain in the source through at least one post-return bot tick (or be
retained as history) so the bot can observe it. A missing,
unreadable, or malformed configured availability source suppresses lifecycle
actions for that tick; the bot does not guess that the decision owner is
available.

The old `decision_inbox.thresholds` keys remain schema-tolerated for existing
installations but are deprecated and ignored. They do not activate this
lifecycle and retain no abandon or auto-close semantics.

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
4. Update tick metrics: `inbox`, `inbox_resolved`, `issues_deferred` (the exact counter names — see Metrics below).

## Resolution states

When closed, an Issue has exactly one of these labels (in addition to `decision:pending-owner` which gets removed):

| Label                  | Meaning                                       |
|------------------------|-----------------------------------------------|
| `decision:approved-A`  | Owner approved option A — bot merged the PR.  |
| `decision:approved-B`  | Owner approved option B (alternate).          |
| `decision:rejected`    | Owner rejected; PR closed.                    |

An open issue may instead carry `decision:deferred` (the owner chose `/approve C`
— defer; nothing merges and the issue stays open until they flip it) or
`decision:stale-approval` (the PR head moved after a verdict — the prior approval
is voided once and the issue waits for a fresh decision). There is **no**
automated abandon / auto-close lifecycle: even an installation that enables
the optional reminder lifecycle leaves the issue open until the owner acts.

## Allowlist enforcement

Only reactions/comments by users in `config/owner.yml` `allowlisted_actors` count. This prevents:

- An agent commenting `/approve A` on its own PR's inbox issue (the agent's bot login is not in the allowlist).
- A spam account creating accounts and 👍-bombing inbox issues.
- A previously-trusted teammate whose account was compromised post-hoc (the allowlist is checked at each tick, not at issue-open time).

The allowlist is `config/owner.yml` `allowlisted_actors` — typically `[<owner-github-login>]` for solo operators, plus optional delegated reviewers.

## Asynchronous by design

Inbox issues are designed for **asynchronous** response, not real-time — the bot
does not page the owner. By default there is **no** timer at all; an explicitly
enabled optional lifecycle may add one-time reminder/escalation comments, but
never abandon or auto-close. An issue stays open until the owner resolves it
(approve / reject / defer). If the PR head moves while an issue is open, the
bot voids the prior approval once and labels the issue
`decision:stale-approval`, so a stale verdict is never applied to unreviewed
code.

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

Every cron tick's metrics counters (the `metrics_summary` artifact) carry
the inbox-relevant keys — these are the exact names the code emits:

```json
{
  "inbox": <int>,           // Quadrant-D issues opened this tick
  "inbox_resolved": <int>,  // owner answers collected this tick
  "issues_deferred": <int>  // "/approve C" needs-more-info deferrals
}
```

There is no `abandoned` counter — no automated abandon lifecycle exists (see
"Asynchronous by design" above). A healthy inbox stays under ~10 open issues
with nothing waiting longer than about a week; sustained higher numbers
indicate either an over-loaded owner or a classifier that is
over-quadrant-D'ing — audit the path rules first. Leave the optional lifecycle
off unless an installation deliberately wants those notification comments.
