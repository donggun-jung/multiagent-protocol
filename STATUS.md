# Status — what works today, what is planned

Updated 2026-05-30. Authoritative when the README banner cites it.

This file is the single source of "what does `multiagent-protocol` actually do,
today, when an external operator forks and installs it?". The README and
concept docs describe the **target** design; this file describes the
**shipping** behaviour. As of **v0.9.9** (the 1.0 release candidate) the cron
orchestrator is live: a fork evaluates PRs, merges the auto-approvable
quadrants, routes Quadrant D to the owner, and audits `main`. This RC is out
for external review; see the "1.0 scope decisions" section below for the items
reviewers are asked to rule on.

## Implementation matrix

| Feature                                            | Doctrine        | v0.0.2 | v0.9.9 (RC, current) |
|----------------------------------------------------|-----------------|--------|----------------------|
| 4-quadrant classifier (path heuristic, max-vote)   | implemented     | ✅      | ✅                |
| 4-module bot package layout                        | implemented     | ✅      | ✅                |
| 5-tier file taxonomy                               | documented      | ✅      | ✅                |
| Skills plugin loader (Validator/Rule/Hook)         | implemented     | ✅      | ✅                |
| Skills loader: AST-based network-import refusal    | implemented     | ✅      | ✅                |
| Skills loader: 1-second wall-clock budget          | documented      | ❌      | ❌ planned        |
| Cron orchestrator: per-tick PR/branch/drift loop   | documented      | ❌ skeleton | ✅            |
| `python -m multiagent_protocol` entry point        | implemented     | ❌ broken (no `__main__`) | ✅ |
| L1.C1 ready-to-merge label                         | implemented     | ✅      | ✅                |
| L1.C1 label-actor allowlist check (timeline)       | implemented     | 🚧      | ✅                |
| L1.C2 required-check CI green                       | implemented     | ✅      | ✅                |
| L1.C3 owner-approval validator (wired to verdict)  | documented      | ❌      | ✅                |
| L1.C4 base up-to-date                              | implemented     | ✅      | ✅                |
| L1.C5 Agent-* trailer format                       | implemented     | ✅      | ✅                |
| L3 race-guard (server-side sha precondition)       | implemented     | ✅      | ✅                |
| L3 auto-rebase stale PR branch (`update-branch`)   | implemented     | ❌      | ✅                |
| L4 trailer registry lookup (tool/model)            | documented      | ❌      | ✅ advisory (P2)  |
| L4 60-day burn-in auto-promotion                   | documented      | ❌      | ❌ (manual via `severity_overrides`) |
| L2 post-merge re-validation                        | documented      | ❌      | ✅ detection      |
| L2 infra-failure differentiation                   | documented      | ❌      | ✅                |
| L2 auto-revert PR **creation**                     | documented      | ❌      | ❌ (incident + revert cmd; see § L2) |
| L5 break-glass detection                           | implemented     | ✅      | ✅                |
| L5 24-hour ADR deadline check (adr_finder wired)   | implemented     | ❌      | ✅                |
| Decision Inbox: open Quadrant D issue (idempotent) | implemented     | 🚧 helpers | ✅             |
| Decision Inbox: poll reactions/comments → resolve  | implemented     | 🚧 helpers | ✅             |
| Decision Inbox: head-SHA tamper detection          | implemented     | ✅      | ✅                |
| Mirror cascade: drift detection                    | implemented     | ✅      | ✅                |
| Mirror cascade: auto-fix PRs                       | not in scope    | ❌      | ❌ (post-v1.0)    |
| Config: owner/projects/env/skills/agent_registry   | implemented     | ✅      | ✅                |
| Config: skills.disabled enforcement                | documented      | ❌      | ✅                |
| Config: skills.severity_overrides enforcement      | documented      | ❌      | ✅                |
| Static web wizard (no backend)                     | implemented     | ✅      | ✅                |
| Wizard: agent-assist prompt + manifest URL         | implemented     | ✅      | ✅                |
| Personal-data CI scan + no-config-in-public guard  | implemented     | ✅/—   | ✅                |
| English + Korean README                            | implemented     | ✅      | ✅                |
| Concept docs Korean mirror                         | partial         | ❌      | ❌ EN only (v1.x) |
| GitHub Pages site                                  | implemented     | ✅      | ✅                |
| Config: `env.allow_no_ci` (no-CI repos auto-merge) | implemented     | ❌      | ✅ (opt-in)           |
| Release pipeline: GHCR image on tag                | implemented     | ❌      | ✅ (`release.yml`)    |
| PyPI publish (OIDC trusted publishing)             | implemented     | ❌      | 🚧 gated to v1.0.0    |
| Composite GitHub Action (`action.yml`)             | implemented     | ❌      | ✅                    |

Legend: ✅ shipped · 🚧 partial · ❌ not yet. *planned* means we intend to ship
it on the version listed.

Test coverage: **140 pytest cases** (was 110 at v0.0.2) — orchestrator
decisions, L2 re-validation, L4 registry, Decision-Inbox resolution, runtime
toggles, and the no-secrets no-op are now under test.

## What it means to fork this today (v0.2.0)

If you fork at v0.2.0 and follow the [Quick start](docs/guide/quick-start.md),
you get a **working gate**:

- The cron tick lists open PRs, classifies each, evaluates L1 (label by an
  allowlisted actor, CI green, base up-to-date, identity trailers), and:
  - **merges** Quadrant A/B/C PRs that pass (squash), opening a passive audit
    issue for B/C;
  - opens a **Decision Inbox issue** for Quadrant D and merges once you 👍 /
    `/approve`;
  - posts a **diagnostic comment** (de-duplicated) when a PR is blocked.
- `main` is audited every tick: **L5** break-glass (author allowlist + ADR
  within 24h) and **L2** post-merge re-validation (real-failure → incident
  issue with the revert command).
- **Mirror drift** across your supervised repos opens a drift incident.
- The tick is a **graceful no-op** when run without `MERGE_GATE_*` secrets, so
  the public upstream's scheduled job does not fail.

### What you will NOT get yet

- **Automatic revert PRs.** L2 detects a bad merge and files an incident with
  the `git revert <sha>` command; it does not yet open the revert PR itself
  (the bot authoring commits in a supervised repo is a Quadrant-D action that
  needs its own ADR — same gating rationale as mirror auto-cascade).
- **Automatic L4 burn-in.** The registry gate ships advisory (P2). Promote it
  to a hard block today with `config/skills.yml` `severity_overrides:
  {validator_agent_registry: P0}`; the automatic 60-day promotion is later.
- **Korean mirror of the concept docs** (README + quick-start are mirrored).
- **Multi-account installations.** A single App installation covering your
  governance + supervised repos is the supported shape.

## Design notes that affect "fully usable"

- **CI-green is fail-closed.** With no `required_checks` configured, C2 requires
  at least one completed check that succeeds; a repo with *no* CI will not
  auto-merge (by design — "no gate" should not silently mean "merge anything").
  Add any one status check, configure required checks, or set `env.yml`
  `allow_no_ci: true` to opt into a vacuous C2 for repos with no CI by design.
- **Statelessness + idempotency.** The bot keeps no DB. Watermarks persist
  within a run and via the tick artifact; across runs, duplicate incidents are
  prevented by checking for an existing open issue referencing the same commit,
  not by durable state.

## 1.0 scope decisions for external review

These are deliberately deferred from the RC. Reviewers are asked to rule on
whether each is a **1.0 blocker** or **post-1.0**:

1. **L2 automatic revert-PR creation.** L2 ships as detection + incident (with
   the `git revert` command). Having the bot author a revert PR in a supervised
   repo is itself a Quadrant-D action needing its own ADR + integration tests —
   same gating rationale as mirror auto-cascade. *Proposed: post-1.0.*
2. **L4 automatic 60-day burn-in.** The registry gate ships advisory (P2);
   promote to hard-block today via `severity_overrides`. The automatic
   advisory→block clock is unbuilt. *Proposed: post-1.0 (manual promote suffices).*
3. **Korean mirror of the concept docs.** README + quick-start are mirrored; the
   nine `docs/concepts/*` are EN-only. *Proposed: nice-to-have for 1.0.*
4. **Multi-account App installations.** Single-account (governance + supervised
   under one installation) is the supported shape. *Proposed: post-1.0.*

The release pipeline (PyPI / Docker / Action) is scaffolded; the only remaining
PyPI step is the owner configuring a trusted publisher (an account action).

## How this file gets updated

Every PR that flips a 🚧 or ❌ to ✅ updates the matrix row in the same diff.
`tests/test_doctrine_consistency.py` keeps doc-vs-code claims from drifting.

## Related

- [README.md](README.md) — what the project is for.
- [CHANGELOG.md](CHANGELOG.md) — per-release diff.
- [docs/concepts/architecture.md](docs/concepts/architecture.md) — the design this matrix grades.
