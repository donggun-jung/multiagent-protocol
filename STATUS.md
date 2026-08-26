# Status — what works today, what is planned

Updated 2026-08-26. Authoritative when the README banner cites it.

This file is the single source of "what does `multiagent-protocol` actually do,
today, when an external operator installs it?". The README and
concept docs describe the **target** design; this file describes the
**shipping** behaviour. As of **v1.5.0** the cron orchestrator is live: your
private installation (a **mirror**, not a fork — see the quick start) evaluates
PRs, merges the auto-approvable quadrants, routes Quadrant D to the
owner, and audits `main` — and the standard install path is **delegated**: your
own AI agent executes [`docs/agent-setup/AGENT_SETUP.md`](docs/agent-setup/AGENT_SETUP.md).
The "1.0 scope decisions" section below records what
is intentionally left for a later release.

## Implementation matrix

| Feature                                            | Doctrine        | v0.0.2 | v1.5.0 (current) |
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
| L4 60-day burn-in auto-promotion                   | implemented     | ❌      | ✅ v1.2 opt-in (`l4_burn_in_days`; explicit `severity_overrides` always wins) |
| L2 post-merge re-validation                        | documented      | ❌      | ✅ detection      |
| L2 infra-failure differentiation                   | documented      | ❌      | ✅                |
| L2 auto-revert PR **creation**                     | implemented     | ❌      | ✅ v1.2 opt-in (`auto_revert_pr`; the revert PR still passes the gate) |
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
| Concept docs Korean mirror                         | implemented     | ❌      | ✅ v1.2 (all 9, EN authoritative on conflict) |
| GitHub Pages site                                  | implemented     | ✅      | ✅                |
| Config: `env.allow_no_ci` (no-CI repos auto-merge) | implemented     | ❌      | ✅ (opt-in)           |
| Release pipeline: GHCR image on tag                | implemented     | ❌      | ✅ (`release.yml`)    |
| PyPI publish (OIDC trusted publishing)             | implemented     | ❌      | 🚧 gated behind `PYPI_PUBLISH_ENABLED` (publisher not configured yet) |
| Composite GitHub Action (`action.yml`)             | implemented     | ❌      | ✅                    |
| Delegated install runbook (`docs/agent-setup/`)    | implemented     | ❌      | ✅ v1.1               |
| Deployed-workflow example (`deploy/`)              | implemented     | ❌      | ✅ v1.1               |
| Operator preferences layer (`config/preferences.yml` + schema) | implemented | ❌ | ✅ v1.1              |
| Adopter agent kit (`templates/adopter/`)           | implemented     | ❌      | ✅ v1.1               |
| Wizard v2 (preferences step + delegation prompt)   | implemented     | ❌      | ✅ v1.1               |
| `verify-setup` deployed-state audit                | implemented     | ❌      | ✅ v1.3 (read-only re-check: App coverage, workflow, labels, squash, kit, placeholders) |
| Gate-liveness check                                | implemented     | ❌      | ✅ v1.3 (last-tick age vs 2× cadence; WARN plain, FAIL in `--e2e`; pull-based) |
| Version-truth parity tests                         | implemented     | ❌      | ✅ v1.3 (pyproject == CHANGELOG / README badges / STATUS header / action pin) |
| Exact-object declared-state completion subreceipt  | implemented     | ❌      | ✅ v1.5 (registry/product remote-main bindings; never live-deploy authorization) |
| Version-contract downgrade + fixed-state-path guard | implemented    | ❌      | ✅ v1.5 (exact baseline OID + accepted superseding ADR evidence) |
| Adopter-kit external-content trust boundary        | implemented     | ❌      | ✅ v1.3 (kit rule 6; defense-in-depth, gate remains the backstop) |

Legend: ✅ shipped · 🚧 partial · ❌ not yet. *planned* means we intend to ship
it on the version listed.

Test coverage: **650+ pytest cases** (was 110 at v0.0.2, 167 at the RC, 435 at v1.1, 465 at v1.2) —
orchestrator decisions, L2 re-validation, L4 registry, Decision-Inbox
resolution, runtime toggles, required-checks threading, published-verdict
rule, unauthorized-push hook, and the no-secrets no-op are under test.

## What it means to install this today (v1.5.0)

If you install at v1.5.0 — delegated ([AGENT_SETUP](docs/agent-setup/AGENT_SETUP.md))
or manual ([Quick start](docs/guide/quick-start.md)) —
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
- `check-project --completion` emits a schema-backed declared-state
  subreceipt from exact remote Git objects, and `check-registry` blocks
  unapproved parity-contract transitions against an exact base commit.
- The tick is a **graceful no-op** when run without `MERGE_GATE_*` secrets, so
  the public upstream's scheduled job does not fail.

### What you will NOT get yet

- **Multi-account installations.** A single App installation covering your
  governance + supervised repos is the supported shape.
- **PyPI installs.** Install from your private mirror; the release job's PyPI
  step stays gated behind `PYPI_PUBLISH_ENABLED` until a trusted publisher
  exists.
- **GitLab / Bitbucket.** The API client is GitHub-specific.
- **Live deployment attestation.** The v1.5 completion profile does not verify
  a deployment instance, endpoint readback, artifact provenance, trusted nonce,
  or authoritative deployment sequence; `completion_authorized` is always
  false. See [`docs/guide/version-truth-completion.md`](docs/guide/version-truth-completion.md).

*(Moved out of this list in v1.2: automatic revert PRs and the automatic
60-day L4 burn-in — both shipped as default-off opt-ins with their own ADRs
(`docs/decisions/0002`, `0003`) — and the Korean concept-doc mirror, now
complete for all nine documents.)*

## Design notes that affect "fully usable"

- **CI-green is fail-closed.** With no `required_checks` configured, C2 requires
  at least one completed check that succeeds; a repo with *no* CI will not
  auto-merge (by design — "no gate" should not silently mean "merge anything").
  Add any one status check, configure required checks, or set `env.yml`
  `allow_no_ci: true` to opt into a vacuous C2 for repos with no CI by design.
- **Statelessness + idempotency.** The bot keeps no DB. Durable watermarks
  live on a dedicated `bot-state` branch in the governance repo (plus the
  tick artifact for audit); duplicate incidents are additionally prevented by
  checking for an existing issue (open or closed) referencing the same commit.

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
