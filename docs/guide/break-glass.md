---
title: Break-glass — overriding the bot
---

# Break-glass — when and how to override the bot

> **This page is the "how do I use it" companion to the doctrine in [`docs/concepts/break-glass.md`](../concepts/break-glass.md).** Read the concept doc first if you have not — it explains *why* break-glass exists and what counts as a permitted use.

The protocol's whole point is that no PR reaches `main` without passing L1. Break-glass is the escape hatch for the cases where the gate itself is broken. Using it costs you an ADR within 24 hours and shows up in the project's permanent audit log.

## The three permitted reasons (in plain English)

| Reason code              | Plain English                                                         |
|--------------------------|-----------------------------------------------------------------------|
| `bot-self-update`        | The bot's own code needs to change, and you cannot use the bot to gate that change (chicken-and-egg). |
| `actions-outage`         | GitHub Actions itself is down for over 2 hours and you have a time-critical fix. |
| `security`               | An active security incident requires an immediate fix that cannot wait for the normal gate. |

Other codes are permitted (the L5 auditor accepts any `^\[break-glass-[a-z0-9-]+\]\s` prefix) but the three above are the ones you should not have to justify in the ADR beyond "this matched the documented criteria for {code}".

## How to do it (3 minutes)

```bash
# 1. Make the change locally.
cd <your-repo>
git checkout main
# ... edit files ...

# 2. Commit with the break-glass prefix.
git commit -m "[break-glass-bot-self-update] Fix classifier publisher slug case-sensitivity

Body explaining what specifically broke and why this could not go through
the normal gate. The ADR (next step) will repeat this with more structure.

Agent-Tool: manual
Agent-Model: n/a
Agent-Session: s_break-glass-fix
Agent-Machine: <your-machine>
Task-Ref: none
"

# 3. Push directly to main.
git push origin main
```

The L5 auditor will see the commit on the next cron tick (~5 minutes). It opens a `decision:break-glass-unaudited` Issue, which auto-closes after the next step.

## How to write the ADR (15 minutes)

Within 24 hours of the break-glass commit, file an ADR at `docs/decisions/NNNN_<topic>.md` where `NNNN` is the next available 4-digit number:

```markdown
---
schema_version: 1
adr_number: 0007
title: "Break-glass: classifier publisher slug case-sensitivity"
status: accepted
date: 2026-05-26T14:32:00Z
authors: ["<your-github-login>"]
supersedes: null
related: []
break_glass:
  commit_sha: "abcd1234567890fedcba9876543210fedcba9876"
  reason_code: "bot-self-update"
  was_alternative_considered: true
  alternative_rejected_because: |
    The fix to the classifier itself cannot pass the bot's own L1 evaluation
    while the classifier is broken — every PR including the fix PR returns
    Quadrant D, and Decision Inbox cannot route because the classifier
    publisher-identity gate is failing closed.
---

## Context

What was happening that required break-glass.

## Decision

What you actually did. Past tense. Cite the commit SHA.

## Consequences

What is now true that was not before. What follow-up is needed (e.g. "next
release MUST include a test that exercises this code path so the bot does
not need to be break-glassed for this reason again").

## Alternatives considered

At minimum: "fix the bot through the normal PR gate first" — explain why
that was not viable in the moment.
```

Open a PR with the ADR. The PR runs through the classifier as Quadrant B (a critical doc, but reversible). It merges via the normal gate. The L5 auditor sees the ADR on the next tick after merge and closes the `decision:break-glass-unaudited` Issue.

## What if I miss the 24-hour deadline?

The Issue stays open with `decision:break-glass-unaudited`. The next agent reading the project sees an unaudited break-glass and knows there is a missing rationale. Practically:

- The bot does not revert the commit. Break-glass is for situations where you needed the change to land, and that decision stands.
- The Issue is a permanent record that the project's history has a gap. Filing the ADR late is still better than not filing.
- Repeated late-ADRs (more than 2 in a month) is a warning sign — see [`docs/concepts/break-glass.md`](../concepts/break-glass.md) § "Frequency budget".

## Counter-example: things that are NOT break-glass

- **"My PR has a malformed Agent-Session trailer and I cannot be bothered to fix it."** Fix the trailer. It is a 30-second amendment.
- **"CI is flaky and I want to skip it."** Make CI deterministic, or relax the required check via a Quadrant D PR.
- **"The classifier said Quadrant D and I think it's wrong."** Answer the Decision Inbox issue. If you think the classifier rule is misbehaving, open a Quadrant D PR that changes the rule.
- **"It is the owner; the gate is silly."** Either change the gate (Quadrant D PR to change a concept doc + the implementing skill in the same PR) or accept it. Break-glass is not "I disagree with the protocol today" — it is "the protocol is mechanically broken right now."

## Bot self-update flow specifically

Because the bot does not gate its own PRs (chicken-and-egg), every bot-repo change is structurally a break-glass:

1. Edit the bot code locally on a feature branch.
2. Once you are confident, run the bot's own pytest suite (`pytest tests/`).
3. Push the change as a regular PR — but be aware the bot's L1 evaluator (running an older version of itself) will **not** evaluate this PR, because the bot's own repo is in the classifier's `bot_self_repo` rule.
4. You merge the bot-repo PR manually using your owner credential. The merge commit SHA is the break-glass commit's SHA.
5. File the ADR with `reason_code: bot-self-update`.
6. The L5 auditor (running the *new* bot code, since the PR is now merged) closes the audit Issue.

This flow is acknowledged-but-imperfect. Whether to add a multi-App architecture that would let the bot self-merge with explicit per-PR human approval (instead of a direct merge) is an open design question; see [`docs/concepts/break-glass.md`](../concepts/break-glass.md) § "Why allow break-glass at all?".

## Related

- [`docs/concepts/break-glass.md`](../concepts/break-glass.md) — the doctrine this guide implements.
- [`docs/concepts/four-quadrants.md`](../concepts/four-quadrants.md) — what counts as "critical" and "irreversible".
- [`docs/concepts/general-preferences.md`](../concepts/general-preferences.md) § 8 — the 24-hour ADR rule as a built-in default.
