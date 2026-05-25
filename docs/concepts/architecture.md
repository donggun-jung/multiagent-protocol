# Architecture

This document defines the **4-module bot** that is the heart of `multiagent-protocol`. The bot is deliberately split into four modules — not five layers — because the consolidation reduces inter-module API surface area by about a third without losing any of the L1–L5 enforcement.

Each module is described below with its inputs, outputs, and the test fixtures it owns.

## Module map

```
┌─────────────────────────────────────────────────────────────────┐
│  bot/main.py — cron entry point (5-min tick)                    │
└────────┬───────────────────────────────────────────────┬────────┘
         │                                               │
         ▼                                               ▼
┌────────────────────┐                          ┌────────────────────┐
│ pr_validator.py    │                          │ branch_supervisor  │
│                    │                          │                    │
│ Per open PR:       │                          │ Per main branch:   │
│  L1 pre-merge gate │                          │  L2 post-merge     │
│  L3 race guard     │                          │     re-validate    │
│  L4 identity gate  │                          │  L5 break-glass    │
│                    │                          │     audit          │
└─────────┬──────────┘                          └─────────┬──────────┘
          │                                               │
          ├────────────────┐                              │
          ▼                ▼                              ▼
┌────────────────┐  ┌──────────────────┐    ┌──────────────────────┐
│ classifier.py  │  │ decision_inbox.py│    │  drift_check.py      │
│                │  │                  │    │                      │
│ A/B/C/D verdict│  │ Quadrant D → Issue│    │  Canonical mirror    │
│ + audit log    │  │ + owner approval │    │  SHA-256 cross-check │
└────────────────┘  └──────────────────┘    └──────────────────────┘
          ▲                ▲                              ▲
          │                │                              │
          └────────────────┴──────────────┬───────────────┘
                                          │
                                  ┌───────┴────────┐
                                  │ skills loader  │
                                  │ + plugins      │
                                  └────────────────┘
```

## Module 1: `pr_validator.py` (L1 + L3 + L4)

Runs once per open PR per cron tick. The three layers it consolidates are tightly coupled (all read PR head SHA, base SHA, check-runs, and commit metadata) so a single module is more honest than three thin wrappers.

### Inputs

- `pr_number`
- `github_api` client (authenticated as the bot's App installation token)
- `config.classifier_rules` (for the auto-approval decision)
- `config.agent_registry` (for trailer validation)

### L1 — Pre-merge gate (5 conditions)

A PR merges only if **all five** of:

| # | Name              | Pass condition                                                                |
|---|-------------------|-------------------------------------------------------------------------------|
| C1| `ready-to-merge`  | Label `ready-to-merge` present, applied by an allowlisted actor (owner only). |
| C2| CI green          | All required status checks completed with `conclusion == "success"`.          |
| C3| Approval          | Either (a) owner reaction/comment on the PR, OR (b) classifier returns A/B/C. |
| C4| Base up-to-date   | PR's base SHA equals current `main` HEAD (no rebase needed).                  |
| C5| Identity trailers | Every PR commit has all 5 `Agent-*` + `Task-Ref` trailers, well-formed.       |

Failure produces a diagnostic comment listing every failing condition (not just the first). Bot re-evaluates on the next tick.

### L3 — Race guard

After L1 passes and just before the merge API call, re-fetch `main` HEAD. If it has advanced since C4 was checked, abort the merge, auto-rebase the PR branch, and let the next tick re-evaluate. This prevents "merged a PR against a stale base" race.

### L4 — Identity gate

Every commit must have:

- `Agent-Tool: <one of agent_registry.tools>`
- `Agent-Model: <one of agent_registry.models[Agent-Tool]>` (or `n/a` for `manual`/`github-actions`)
- `Agent-Session: s_[a-z0-9-]{4,16}[a-z0-9]` (ends in alphanumeric)
- `Agent-Machine: <one of agent_registry.machines>` (free-form; registered values get extra trust signals)
- `Task-Ref: (Issue#N|PR#N|none|round-X/<topic>|bot/<topic>)`

L4 has a 60-day **burn-in** window before promotion from advisory to hard-block (see `docs/concepts/four-quadrants.md` § "L4 burn-in").

### Outputs

- Either: a merge (via GitHub API, recording the head SHA as merge precondition to defeat TOCTOU).
- Or: a diagnostic comment + repeat next tick.
- Or: a `merge-gate-failure` label + `decision:pending-owner` Issue (Quadrant D path; see `decision_inbox.py`).

## Module 2: `branch_supervisor.py` (L2 + L5)

Runs once per supervised repo per cron tick. Operates on `main` HEAD, not on open PRs.

### L2 — Post-merge re-validation

For each commit on `main` newer than the last `branch_supervisor` watermark:

1. Re-run the L1 required-checks set against the merged commit's SHA.
2. If all pass: advance watermark, no action.
3. If some fail **and the failure is real** (see "infra-failure differentiation" below): open a revert PR. Label it `decision:auto-revert`.
4. If failures are **all infra-failure**: record `infra-failure` state in the tick metrics, do **not** revert, retry on next tick.

#### Infra-failure differentiation

A failed check is considered infra-failure (not a real failure) when:

- `conclusion == "cancelled"` (workflow killed mid-run — e.g., Actions minutes exhaustion), OR
- `started_at == completed_at` (zero-duration → never executed, runner queue rejected).

`conclusion == "skipped"` is **not** infra-failure: it means the workflow's own `if:` condition evaluated false (intentional protocol skip). Treating skipped as infra was a known false-negative in earlier designs.

### L5 — Break-glass auditor

For each commit on `main` whose subject matches `^\[break-glass-[a-z0-9-]+\]`:

1. Verify the commit author is in `config.owner.allowlisted_actors`.
2. Verify an ADR (Architecture Decision Record) was filed in `docs/decisions/` within 24h of the commit timestamp, referencing the break-glass commit's SHA.
3. If either fails, open a `decision:break-glass-unaudited` Issue tagging the owner.

L5 runs across **all registered repos** including the bot's own repo. The bot does not gate its own PRs (L1-L4 skip the bot repo by design) but its `main` commits are still audited.

### Outputs

- Revert PRs (with `decision:auto-revert` label).
- Break-glass audit Issues.
- Watermarks recorded in repo state (a single file in the bot's own repo).

## Module 3: `decision_inbox.py`

Runs once per cron tick across all supervised repos. Owns the **Quadrant D → owner → resume** loop.

### Quadrant D issue lifecycle

1. **Open**: When `classifier.py` returns Quadrant D for a PR, the bot opens an Issue in `<governance_repo>` labelled `decision:pending-owner` with the PR link and the 4-option ballot (A: approve, B: alternate, C: defer, /reject).
2. **Poll**: Every tick, the inbox checks open `decision:pending-owner` Issues for new owner reactions (👍/👎) or comments (`/approve A`, `/approve B`, `/approve C`, `/reject`).
3. **Resolve**: An owner verdict triggers:
   - Approve → bot returns to PR, re-runs L1 (C3 will now pass), merges if everything else green.
   - Reject → bot closes the PR with a "rejected per Decision Inbox" comment.
4. **Close**: Inbox issue auto-closes when the linked PR merges or closes.

### Stale handling

If a `decision:pending-owner` Issue is open for more than 14 days with no activity, the bot pings the owner via a daily nudge comment. After 30 days, label `decision:abandoned`. No auto-action — only human deletes or revives.

### Outputs

- Issues opened/closed in `<governance_repo>`.
- PR labels (`decision:approved-A`, `decision:approved-B`, `decision:approved-C`, `decision:rejected`).
- Tick metrics: open count, average age, oldest age.

## Module 4: `drift_check.py`

Runs once per cron tick. Enforces that **canonical files** in `<governance_repo>` match byte-for-byte across all adopter repos (mirror cascade).

### Mechanism

1. Read `config.mirror_paths` (list of file paths under `<governance_repo>` that are canonical-of-canonical).
2. For each adopter repo: compute SHA-256 of each canonical path, compare to `<governance_repo>` source-of-truth SHA.
3. Mismatch → `decision:mirror-drift-incident` Issue with diff summary.
4. Missing (canonical-required file absent in adopter) → same Issue, with `missing=true` field.

Drift is **detected**, not auto-fixed. Auto-fix would require opening a PR in each adopter, which is its own classifier path — currently the operator handles drift by re-running the cascade workflow manually. (R14 candidate: auto-cascade PRs.)

### Outputs

- `decision:mirror-drift-incident` Issues.
- Tick metrics: drift count per adopter, missing-file count per adopter.

## Stateless across ticks

The bot is **stateless across cron ticks**. All state lives in GitHub:

- PR state: GitHub PR object.
- Decision Inbox: Issues in `<governance_repo>`.
- Watermarks: a single file `bot-state/branch_supervisor_watermarks.json` in the bot's own repo (the bot pushes a commit to update it; this is the only commit the bot makes to its own repo).
- Audit log: GitHub Actions workflow artifacts (90-day retention) + commit history.

This means each tick re-evaluates from scratch. The drawback is chatty comments on long-running PR failures; the upside is no local DB to corrupt, no migration on bot version upgrade, and disaster recovery is "redeploy the bot" with no state to restore.

## Per-tick cost (rate-limit budget)

At 6 supervised repos × ~5 open PRs × ~10 API calls per PR + L5 self-scan (~50 calls) + drift_check (~30 calls per adopter) = ~430 calls per tick. At 12 ticks/hour, ~5,160 calls/hour. GitHub installation rate limit is 15,000/hour, so the bot uses about 35% headroom even with growth to 12+ repos.

## Plug-in points

The 4 modules call into the **skills loader** (`src/multiagent_protocol/skills/`) for extensible behavior:

- `pr_validator.py` calls `Validator.check(pr_context)` for each registered validator (built-in C1-C5 + user-added).
- `classifier.py` calls `ClassifierRule.evaluate(pr_context)` for each registered rule (returns A/B/C/D vote; rules combined per `config.classifier_rules.combine_mode`).
- `branch_supervisor.py` calls `BranchHook.on_commit(commit)` for each registered hook (built-in: L5 audit; user-added: e.g., changelog enforcer).
- `decision_inbox.py` and `drift_check.py` do not currently call skills, but the interface is reserved.

See [`docs/concepts/skills-plugin.md`](skills-plugin.md) for the plugin interface specification.
