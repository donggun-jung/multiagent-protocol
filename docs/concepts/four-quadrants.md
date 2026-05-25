# The four-quadrant autonomy classifier

When a PR is opened against a supervised repository, the bot must decide:

- **Should this merge automatically?** (Quadrants A, B, C)
- **Or should the human owner be asked first?** (Quadrant D)

The decision is made by combining two **independent axes**: reversibility and criticality. The product yields a 2×2 grid — four quadrants — each with a different default action.

```
                          Reversible       Irreversible
                       ┌──────────────┬──────────────────┐
                       │              │                  │
       Non-critical    │      A       │        C         │
                       │  auto-merge  │  auto-merge      │
                       │              │  (record audit)  │
                       ├──────────────┼──────────────────┤
                       │              │                  │
       Critical        │      B       │        D         │
                       │  auto-merge  │  owner approval  │
                       │  + audit log │  REQUIRED        │
                       │              │                  │
                       └──────────────┴──────────────────┘
```

## Axis 1: Reversibility (IR — "is reversible")

A change is **reversible** if `git revert <sha>` undoes it cleanly with no external side effects. A change is **irreversible** if any of:

- Deletes a file (revert restores it, but git history scars persist).
- Modifies a database schema or external system.
- Sends an email / posts to social media / fires a webhook.
- Touches a secret, credential, or `.env` file.
- Changes the bot's own merge logic or auth.

When in doubt, classify as **irreversible**. False-positive irreversibility costs a Decision Inbox ping; false-negative irreversibility costs an unrecoverable accident.

## Axis 2: Criticality (CR — "is on critical path")

A change is **critical** if it touches any of:

- Bot source code (`src/multiagent_protocol/*`).
- Doctrine documents (`docs/concepts/*`).
- Schemas (`schemas/*.json`).
- Config schemas (the structure, not the values).
- `.github/workflows/*.yml` (CI definitions).
- `LICENSE`, `SECURITY.md`, `MAINTAINERS.md`.

A change is **non-critical** if it touches only:

- Documentation under `docs/guide/`, `docs/concepts/` *only* for typo fixes (not for rule changes).
- `examples/*` (anyone can demo whatever they want).
- `tests/*` adding new test cases (not modifying existing ones).
- `CHANGELOG.md`, comments, READMEs.

When in doubt, classify as **critical**. The cost of false-positive criticality is the same Decision Inbox ping; the cost of false-negative is shipping a broken `pr_validator.py` to every fork.

## The four quadrants

### A — Reversible + Non-critical

Examples: typo fix in README. Adding a new example under `examples/`. Comment-only changes.

**Default action**: bot auto-approves L1.C3. If all other conditions pass, merge.

**Audit**: tick-metrics counter `quadrant_a_count` increments. No issue opened.

### B — Reversible + Critical

Examples: refactoring `pr_validator.py` (the refactor itself is reversible, but the file is critical).

**Default action**: bot auto-approves L1.C3. If all other conditions pass, merge.

**Audit**: a `decision:auto-approved-critical-reversible` Issue opens, body = PR link + classifier reasoning. The Issue auto-closes after 7 days if no owner comment. This gives the owner a passive review trail without blocking the merge.

### C — Irreversible + Non-critical

Examples: deleting a stale doc file. Removing an obsolete example.

**Default action**: bot auto-approves. Merge.

**Audit**: a `decision:auto-approved-irreversible-non-critical` Issue opens (same 7-day passive-close). The asymmetry vs. B is deliberate: irreversibility deserves explicit audit even if the file is non-critical.

### D — Irreversible + Critical

Examples: changing `pr_validator.py`'s merge logic. Modifying a JSON schema. Adding a new built-in skill. Adding a non-trusted agent to `agent_registry.yml`.

**Default action**: bot opens a `decision:pending-owner` Issue. **L1.C3 fails until the owner answers.**

The Issue presents a 4-option ballot:

- **A — Approve as proposed.**
- **B — Approve with alternate (see PR description).**
- **C — Defer / needs more info** (label `decision:deferred`, owner can later flip to A or D-reject).
- **/reject** — close the PR.

Owner responds via 👍/👎 reaction OR comment `/approve A`, `/approve B`, `/approve C`, `/reject`. The bot polls the Issue every cron tick.

## Borderline rules

When IR and CR are not cleanly determined:

1. **Empty PR (no file changes)** → Quadrant D. An empty PR with `ready-to-merge` is suspicious enough to wake the owner.
2. **Multi-quadrant PR** (e.g., one file fits A, another fits D) → take the **highest-quadrant** (D > B > C > A). One critical file taints the whole PR.
3. **Bot's own repo PRs** → Quadrant D regardless of contents (bot does not self-merge; see [`break-glass.md`](break-glass.md)).
4. **Auto-revert PRs** (opened by L2 after detecting post-merge failure) → Quadrant C with label `decision:auto-revert`. These need to land fast.

## L4 burn-in: 60-day advisory window

When a new agent vendor or model is added to `agent_registry.yml`, the L4 identity gate is **advisory** (warns but does not block) for the first 60 days. During burn-in:

- The bot records the new identity in `tick_metrics.l4_burn_in[agent_id]`.
- If the agent never produces a Quadrant-D PR rejection in 60 days, the identity is **promoted** to hard-block status (mismatched trailers fail L1.C5).
- If the agent does produce rejections, the burn-in clock resets.

This avoids the "every new agent vendor breaks every PR" problem when adding `Aider 0.x` or `Codex 2.0` to a fleet that has been using `Claude Code` for months.

## Classifier rule composition

`classifier.py` evaluates each PR by running all registered classifier rules (built-in + user-added) and **taking the maximum quadrant**. Built-in rules:

| Rule                           | Quadrant on match | What it catches                          |
|--------------------------------|-------------------|------------------------------------------|
| `path_classifier_default`      | per axes above    | File-path heuristic                      |
| `bot_self_repo`                | D                 | PR to the bot's own repo                 |
| `empty_pr`                     | D                 | PR with no file diff                     |
| `auto_revert_marker`           | C                 | Label `decision:auto-revert` present     |
| `agent_session_invalid`        | D                 | Any commit's `Agent-Session` malformed   |
| `classifier_publisher_invalid` | D                 | classifier-judgment by non-canonical App |

User-added rules in `config/skills/classifier/*.py` are loaded after built-ins. They cannot **lower** the quadrant (you cannot write a skill that says "this critical change is actually non-critical"), only raise it.

## The audit log

Every classifier decision is appended to a JSONL audit log at `bot-state/classifier_audit.jsonl` in the bot's repo. Entries include:

```json
{
  "ts": "2026-05-25T12:34:56Z",
  "pr": "owner/repo#123",
  "head_sha": "abcd1234...",
  "rules_fired": [
    {"rule": "path_classifier_default", "ir": "reversible", "cr": "critical"},
    {"rule": "empty_pr", "match": false},
    {"rule": "user.no_todos_in_prod", "result": "pass"}
  ],
  "quadrant": "B",
  "reasoning": "src/multiagent_protocol/pr_validator.py modified (critical); revertable file edit (reversible)"
}
```

The log is the source of truth for "why did the bot merge that?" investigations. It is never overwritten — only appended.

## Why this design

The IR/CR axes split the question "who decides?" by **the property of the change**, not by **the kind of file**. Two principles fall out:

1. The bot can be wrong about A or B (cost: a revert PR or an awkward audit issue) but the bot cannot be wrong about D without your knowledge — D forces the question to you.
2. False-positive D ("you asked me about a typo!") is recoverable in seconds (👍 reaction). False-negative D ("the bot merged a database migration without asking!") is potentially unrecoverable. The system is asymmetric on purpose.

If the bot's classifier produces too many false-positive D, the answer is to add a classifier rule that down-weights known-safe paths — not to lower the asymmetry. The asymmetry is the protocol.
