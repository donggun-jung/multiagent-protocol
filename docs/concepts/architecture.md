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
- The loaded `AppConfig` (in particular `config.skills` for severity/enable toggles and `config.agent_registry` for L4 trailer registry lookup)

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
- `Agent-Session: s_[a-z0-9-]{2,14}[a-z0-9]` (ends in alphanumeric)
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

**Shipping status:** L2 ships as **detection + incident**. On a real post-merge failure the bot opens a `decision:post-merge-revalidation` issue carrying the `git revert <sha>` command; it does not yet *author* the revert PR. Having the bot commit to a supervised repo is itself a Quadrant-D action that needs its own ADR — the same rationale that keeps mirror auto-cascade manual. The `classifier_auto_revert` rule + `decision:auto-revert` label are already in place for when a revert PR is opened (by you now, or by a future auto-revert feature). See [`STATUS.md`](../../STATUS.md).

### L5 — Break-glass auditor

For each commit on `main` whose subject matches `^\[break-glass-[a-z0-9-]+\]`:

1. Verify the commit author is in `config/owner.yml` `allowlisted_actors`.
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

There is **no automated nudge / abandon / auto-close timer** (see
[`decision-inbox.md`](decision-inbox.md), which is authoritative here). The
inbox is asynchronous by design: an issue waits as long as the owner needs.
The two stale-related mechanisms that DO exist are label-based: `/approve C`
marks the PR `decision:deferred` (needs more info), and an approval whose PR
head SHA has since changed is superseded with `decision:stale-approval`.
Aging is made *visible* (open count and ages in the tick metrics), never
acted on automatically.

### Outputs

- Issues opened/closed in `<governance_repo>`.
- PR labels (`decision:approved-A`, `decision:approved-B`, `decision:approved-C`, `decision:rejected`).
- Tick metrics: open count, average age, oldest age.

## Module 4: `drift_check.py`

Runs once per cron tick. Enforces that **canonical files** in `<governance_repo>` match byte-for-byte across all adopter repos (mirror cascade).

### Mechanism

1. Read `config.mirror_paths` (list of file paths under `<governance_repo>` that are canonical-of-canonical).
2. For each adopter repo: compare the git **blob SHA** of each canonical path against the `<governance_repo>` source-of-truth blob SHA (equal blob SHA ⇔ byte-identical content). The SHAs come from one recursive-tree fetch per repo per tick — the governance tree is fetched once and reused across all adopters — with a per-path lookup fallback when a tree is unavailable. The governance repo itself is skipped (comparing the canonical to itself is always clean).
3. Mismatch → `decision:mirror-drift-incident` Issue with diff summary.
4. Missing (canonical-required file absent in adopter) → same Issue, with `missing=true` field.

Drift is **detected**, not auto-fixed. Auto-fix would require opening a PR in each adopter, which is its own classifier path — currently the operator handles drift by re-running the cascade workflow manually. (Auto-cascade PRs are a planned post-v1.0 feature, gated on an ADR in `docs/decisions/` that explicitly authorizes the bot to open critical-path PRs in adopters.)

### Outputs

- `decision:mirror-drift-incident` Issues.
- Tick metrics: drift count per adopter, missing-file count per adopter.

## Stateless across ticks

The bot is **stateless across cron ticks**. All state lives in GitHub:

- PR state: GitHub PR object.
- Decision Inbox: Issues in `<governance_repo>`.
- Watermarks: a single file `bot-state/branch_supervisor_watermarks.json` persisted to a dedicated **`bot-state` branch** of the governance repo via the App token (the only commits the bot makes to its own repo). Deliberately **not** `main`: the bot's own L2/L5/unauthorized-push scanners read only `main`, so state commits can never self-trigger an incident. The tick loads this file at start (creating the branch on first run), persists incrementally after each repo and again in a `finally` guard, so a timed-out tick still banks its progress. A repo seen for the first time bootstraps its watermark to the current `main` HEAD and scans nothing older — pre-activation history is out of scope, which is what prevents a cold-start incident flood. A corrupt persisted state fails the tick closed (non-zero) instead of silently re-walking history.
- Audit log: GitHub Actions workflow artifacts (90-day retention) + commit history.

This means each tick re-evaluates from scratch. The drawback is chatty comments on long-running PR failures; the upside is no local DB to corrupt, no migration on bot version upgrade, and disaster recovery is "redeploy the bot" with no state to restore.

## Per-tick cost (rate-limit budget)

At 6 supervised repos × ~5 open PRs × ~10 API calls per PR + the bounded L2/L5 main scans (≤100 commits per repo per tick; a handful of calls when idle) + drift_check (one recursive-tree call per adopter per tick, the governance tree cached) ≈ ~370 calls per tick worst case, ~4,400/hour at 12 ticks/hour. The GitHub App installation rate limit on a personal account is **5,000 requests/hour** (the often-quoted 15,000/hour applies only to installations on GitHub Enterprise Cloud organizations), so the margin is real but thin. The bot therefore watches `X-RateLimit-Remaining` on every response and ends a tick early — after persisting watermarks — when fewer than a reserve threshold of calls remain; a secondary-rate-limit `403`/`429` backs off (honouring `Retry-After`, bounded) and then skips the affected repo for the tick instead of crashing and replaying.

## Plug-in points

The 4 modules call into the **skills loader** (`src/multiagent_protocol/skills/`) for extensible behavior:

- `pr_validator.py` calls `Validator.check(pr_context)` for each registered validator (built-in C1-C5 + user-added).
- `classifier.py` calls `ClassifierRule.evaluate(pr_context)` for each registered rule (returns A/B/C/D vote; the engine takes the **maximum quadrant** across all rules — see `four-quadrants.md` § "Classifier rule composition").
- `branch_supervisor.py` calls `BranchHook.on_commit(commit)` for each registered hook (built-in: L5 audit; user-added: e.g., changelog enforcer).
- `decision_inbox.py` and `drift_check.py` do not currently call skills, but the interface is reserved.

See [`docs/concepts/skills-plugin.md`](skills-plugin.md) for the plugin interface specification.
