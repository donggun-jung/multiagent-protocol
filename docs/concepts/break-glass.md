# Break-glass — bypassing the bot when you must

The whole point of `multiagent-protocol` is that no PR lands on `main` without passing the L1 gate. But there are real situations where the bot itself is broken, the owner is offline, or the gate would deadlock progress. The protocol handles these with **break-glass**: a structured override that leaves a forensic trail.

This document defines:

1. When you may break glass.
2. How to break glass (the commit-subject prefix).
3. What the bot does after you break glass (L5 audit, ADR-within-24h).
4. Why the protocol allows this at all.

## When break-glass is permitted

The owner may push directly to `main`, bypassing the L1 gate, only in these situations:

1. **The bot is broken.** Specifically: `pr_validator.py` is producing wrong verdicts on every PR (not just yours), and the fix requires a bot-repo PR which itself cannot pass the gate (chicken-and-egg). See "Bot self-update flow" below.
2. **GitHub Actions outage.** Required status checks cannot complete because GitHub-side infrastructure is down (Actions queue exhausted, GitHub.com 5xx, etc.) for >2 hours and there is a time-critical fix needed.
3. **Active security incident.** A vulnerability is being actively exploited (leaked key, malicious commit on `main`) and the fix cannot wait for normal gate timing.

Each of these is **rare**. If you find yourself breaking glass more than once a month, the protocol is failing — either the bot is fragile (fix it) or the classifier is wrong (tune it) or you are doing operator work that should be automated (write a skill for it).

## Break-glass is not permitted

- "I need to ship before lunch and the classifier said D" → no. Answer the Decision Inbox issue.
- "I am the owner, the gate is silly" → no. Either change the gate (Quadrant D PR) or accept it.
- "My agent's PR has malformed trailers and I cannot be bothered to fix them" → no. Fix the trailers; this is a 30-second amendment.
- "CI is flaky and I want to skip it" → no. Make CI deterministic or relax the check via a Quadrant D PR.

If you break glass for a non-permitted reason, the L5 audit will flag it and the ADR-within-24h requirement (see below) is your chance to either justify it or revert.

## How to break glass

A direct-to-`main` commit (no PR) with subject matching the regex `^\[break-glass-[a-z0-9-]+\]\s`. Examples:

- `[break-glass-actions-outage] Hotfix L1 to handle empty check-runs list`
- `[break-glass-bot-self-update] Fix classifier publisher slug case sensitivity`
- `[break-glass-security] Revoke leaked PEM, re-issue Actions secret`

The prefix has three parts:

1. `[break-glass-` (literal)
2. A short kebab-case reason code. Recommended codes: `actions-outage`, `bot-self-update`, `security`, `data-recovery`, `legal`.
3. `]` (literal) then a space and a regular conventional-commit message.

The commit must be authored by an actor in `config.owner.allowlisted_actors`. Authorship is checked by L5; an unauthorized actor's break-glass commit triggers a `decision:break-glass-unauthorized` issue immediately.

## What the bot does next

`branch_supervisor.py` runs `L5_break_glass_audit` on every cron tick across `main` HEAD of all registered repos. For each commit matching the break-glass prefix:

1. **Allowlist check.** Author in `config.owner.allowlisted_actors`? If no → open `decision:break-glass-unauthorized` issue, label PR/commit, ping owner. Hard alert.
2. **ADR existence check.** Is there a file under `docs/decisions/` referencing this commit's SHA in its body, with a commit timestamp within the last 24 hours? If no → open `decision:break-glass-unaudited` issue, label, ping. Soft alert (passive — the audit issue stays open until ADR lands).
3. **ADR content check.** Once the ADR exists, parse its frontmatter (`schema_version: 1`, required fields: `adr_number`, `title`, `status`, `date`, `authors`, `supersedes`, `related`, plus a `break_glass:` block with `commit_sha`, `reason_code`, `was_alternative_considered`). If frontmatter is invalid → issue stays open with `decision:break-glass-incomplete-adr`.
4. **Watermark advance.** Once both checks pass, the watermark advances and the commit is no longer surfaced by L5 next tick.

## The ADR-within-24h requirement

Within 24 hours of any break-glass commit, an ADR must land in `docs/decisions/NNNN_<topic>.md` with the structure:

```markdown
---
schema_version: 1
adr_number: <next available number>
title: "Break-glass: <reason>"
status: accepted
date: <ISO-8601 of break-glass commit>
authors: ["<owner-github-login>"]
supersedes: null
related: []
break_glass:
  commit_sha: "<full SHA of the break-glass commit>"
  reason_code: "<one of the recommended codes>"
  was_alternative_considered: true|false
  alternative_rejected_because: "<one paragraph if true>"
---

## Context

<what was happening that required break-glass>

## Decision

<what you actually did, in past tense>

## Consequences

<what is now true that wasn't before; what follow-up is needed>

## Alternatives considered

<at minimum: "fix the bot first" — explain why that was not viable in the moment>
```

The ADR is itself a PR (or a direct commit if you are still in the break-glass window). It runs through the normal classifier — usually Quadrant B (reversible + critical doc). It merges via the gate even though the underlying commit it documents bypassed the gate.

## Bot self-update flow

The bot does not gate its own PRs (chicken-and-egg: the L1 evaluator the bot would use to gate its own PR is the same code being changed). When the bot needs to be updated:

1. Make the change locally.
2. Push a direct commit to `main` of `<bot-repo>` with subject `[break-glass-bot-self-update] <description>`.
3. Open a regular PR with the ADR in `<governance-repo>/docs/decisions/`.
4. L5 sees the bot-repo break-glass commit on next tick. ADR check passes when the governance-repo PR merges (which has the ADR file).

This flow is acknowledged-but-deferred-for-improvement. A possible future ADR (open question — see [`docs/decisions/`](../decisions/) once it lands) would propose a multi-App architecture in which the bot's normal-operation credentials cannot merge to `main` and a separate owner-deploy credential, used only with explicit human approval per PR, would replace the break-glass-bot-self-update flow. Until such an ADR is written and accepted, bot self-update remains the most common (and the only sanctioned) break-glass reason.

## Why allow break-glass at all?

A protocol with no override is brittle. If the bot enters a deadlock (e.g., classifier returns D for every PR including the fix to the classifier), there must be a recovery path or you have to nuke the gate. Break-glass is the recovery path.

The cost of break-glass is the ADR. The cost is intentionally non-zero — writing an ADR within 24h takes 20-30 minutes — to discourage casual use. The audit trail (L5 + ADR) is the protocol's way of saying: "you can override me, but you must explain yourself in writing, and the explanation becomes part of the project's permanent record."

## Frequency budget

A healthy operator breaks glass **less than once per month**. Specific budgets:

- 0 break-glass events per month: ideal. The protocol is doing its job.
- 1-2 per month: normal during early adoption (you are still learning what to put in classifier rules).
- 3-5 per month: warning. Either the bot is fragile or the operator is fighting the protocol. Review the ADRs to find the pattern.
- 6+ per month: failure. The protocol is not working for this operator. Either change the operator's workflow or change the protocol. Open an Issue tagged `protocol-friction` and discuss before continuing.

The bot tracks break-glass frequency in `tick_metrics.l5_break_glass_count_30d`. This number is part of the dashboard you see at `STATUS.md`.
