# Status — what works today, what is planned

Updated 2026-05-26. Authoritative when the README banner cites it.

This file is the single source of "what does v0.0.x of multiagent-protocol actually do, today, when an external operator forks and installs it?". The README and concept docs describe the **target** design; this file describes the **shipping** behaviour.

## Implementation matrix

| Feature                                            | Doctrine        | v0.0.2 (current) | v0.1.0 (next) | v0.2.0       |
|----------------------------------------------------|-----------------|------------------|---------------|--------------|
| 4-quadrant classifier (path heuristic, max-vote)   | implemented     | ✅                | ✅             | ✅            |
| 4-module bot package layout                        | implemented     | ✅                | ✅             | ✅            |
| 5-tier file taxonomy                               | documented      | ✅                | ✅             | ✅            |
| Skills plugin loader (Validator/Rule/Hook)         | implemented     | ✅                | ✅             | ✅            |
| Skills loader: AST-based network-import refusal    | implemented     | ✅                | ✅             | ✅            |
| Skills loader: 1-second wall-clock budget          | documented      | ❌ planned        | 🚧            | ✅            |
| L1.C1 ready-to-merge label                         | implemented     | ✅                | ✅             | ✅            |
| L1.C1 label-actor allowlist check                  | implemented     | 🚧 needs label events from timeline API | ✅             | ✅            |
| L1.C2 required-check CI green                      | implemented     | ✅                | ✅             | ✅            |
| L1.C3 owner-approval validator                     | documented      | ❌ planned        | ✅             | ✅            |
| L1.C4 base up-to-date                              | implemented     | ✅                | ✅             | ✅            |
| L1.C5 Agent-* trailer format                       | implemented     | ✅                | ✅             | ✅            |
| L4 trailer registry lookup (tool/model/machine)    | documented      | ❌ planned        | ✅             | ✅            |
| L4 60-day burn-in                                  | documented      | ❌ planned        | ❌             | ✅            |
| L3 race-guard (server-side sha precondition)       | implemented     | ✅                | ✅             | ✅            |
| L2 post-merge re-validation                        | documented      | ❌ planned        | ❌             | ✅            |
| L2 infra-failure differentiation                   | documented      | ❌ planned        | ❌             | ✅            |
| L5 break-glass detection                           | implemented     | ✅                | ✅             | ✅            |
| L5 24-hour ADR deadline check                      | documented      | ❌ planned (no commit timestamp in context) | ✅             | ✅            |
| Decision Inbox: open Quadrant D issue              | implemented     | 🚧 helpers only  | ✅             | ✅            |
| Decision Inbox: poll reactions/comments → resolve  | implemented     | ✅ helpers; orchestrator wires in v0.2 | ✅             | ✅            |
| Decision Inbox: head-SHA tamper detection          | implemented     | ✅                | ✅             | ✅            |
| Mirror cascade: drift detection                    | implemented     | ✅                | ✅             | ✅            |
| Mirror cascade: auto-fix PRs                       | not in scope    | ❌                | ❌             | ❌ (post-v1.0)|
| Cron orchestrator: per-tick PR/branch/drift loop   | documented      | ❌ skeleton       | 🚧            | ✅            |
| Static web wizard (no backend)                     | implemented     | ✅                | ✅             | ✅            |
| Wizard: agent-assist prompt                        | implemented     | ✅                | ✅             | ✅            |
| Wizard: GitHub App Manifest 1-click URL            | implemented     | ✅                | ✅             | ✅            |
| Config: owner/projects/env/skills/agent_registry   | implemented     | 🚧 4 of 5 loaded; agent_registry loader in v0.1 | ✅             | ✅            |
| Config: skills.disabled enforcement                | documented      | ❌ planned        | ✅             | ✅            |
| Config: skills.severity_overrides enforcement      | documented      | ❌ planned        | ✅             | ✅            |
| JSON Schema for every config file                  | implemented     | ✅                | ✅             | ✅            |
| Hallucination guard (built-in skill, default-on)   | implemented     | ✅                | ✅             | ✅            |
| Classifier publisher identity gate                 | implemented     | ✅                | ✅             | ✅            |
| Personal-data CI scan                              | implemented     | ✅                | ✅             | ✅            |
| English + Korean README                            | implemented     | ✅                | ✅             | ✅            |
| Concept docs Korean mirror                         | partial         | ❌ EN only; KO mirror v0.2 | partial       | ✅            |
| GitHub Pages site                                  | implemented     | ✅ Jekyll-rendered markdown | ✅             | ✅            |
| PyPI release                                       | planned         | ❌                | ❌             | 🚧 alpha     |
| Docker image                                       | planned         | ❌                | ❌             | 🚧 alpha     |
| Published GitHub Action                            | planned         | ❌                | ❌             | 🚧 alpha     |

Legend: ✅ shipped · 🚧 partial · ❌ not yet · *planned* means we intend to ship it on the version listed.

## What it means to fork this today

If you fork at v0.0.2 and follow the [Quick start](docs/guide/quick-start.md), you will get:

- A repo that **passes lock-guard CI** with a working schema/example/skill loader.
- A **wizard** that builds correct config YAML + a working GitHub App Manifest URL.
- A **GitHub App** that you can install on your supervised repos.
- A **cron workflow** that fires every 5 minutes.

What you will **not** get yet:

- The cron tick does not iterate over open PRs and post diagnostic comments. The orchestrator loop is a skeleton — it loads config, authenticates, lists App installations, then exits. **PR evaluation lands in v0.2.0.**
- Post-merge re-validation (L2) does not run. The `branch_supervisor.py` module ships with L5 only; L2 ships in v0.2.0.
- Quadrant D PRs do not open Decision Inbox issues automatically. The helpers exist (`open_inbox_issue`, `resolve_verdict`) but are not yet wired into the cron loop — also v0.2.0.
- The Agent-* trailer registry (`agent_registry.yml`) is loaded by the loader but **not consulted** by the validator. v0.1.0 wires the lookup.

If any of those four items are blockers for your use case, watch this file — we will mark them ✅ in v0.1.0 or v0.2.0 respectively, with a CHANGELOG entry pinning the commit SHA that turned the box green.

## Why ship v0.0.2 at all when the cron loop is empty

Three reasons:

1. **The doctrine + schemas + plugin contract are the load-bearing parts of this project.** They take longer to design than to implement. Shipping them gives the next contributor (and the next reviewer) a stable surface to push against.
2. **The wizard works end-to-end today.** An operator can already generate correct config, register the App, and have the cron workflow run. The missing piece is one Python loop body, not a missing architecture.
3. **It is more honest to ship a partial alpha than to wait until everything works.** Hidden internal milestones do not get external review. A public alpha that says "v0.2.0 ships the orchestrator, here is the file it lives in" invites the kind of pull request that closes the gap.

## How this file gets updated

- Every PR that flips a 🚧 or ❌ to ✅ **must** include the matrix row update in the same diff. CI does not enforce this yet (it will after v0.1.0 once `tests/test_doctrine_consistency.py` lands), but the maintainer rejects PRs that ship code without updating the matrix.
- Version columns shift right as releases ship. When v0.1.0 ships, the "v0.1.0 (next)" column becomes "v0.1.0 (current)" and a new "v0.2.0 (next)" column appears.

## Related

- [README.md](README.md) — what the project is for.
- [CHANGELOG.md](CHANGELOG.md) — per-release diff.
- [MAINTAINERS.md](MAINTAINERS.md) — best-effort maintenance policy.
- [docs/concepts/architecture.md](docs/concepts/architecture.md) — the design that this matrix grades.
