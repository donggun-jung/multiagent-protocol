# multiagent-protocol

**Self-built branch protection and decision routing for solo developers and small teams who use multiple AI coding agents (Claude Code, Codex, Cursor, Gemini-CLI, …) on the same GitHub repositories.**

> One human, many agents. Different sessions, different machines, different models — same `main` branch. This protocol stops them from stepping on each other, gates merges through a self-built check, and routes irreversible decisions back to you instead of letting agents decide.

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Status: v1.0](https://img.shields.io/badge/status-v1.0-brightgreen.svg)](STATUS.md)
[![Docs](https://img.shields.io/badge/docs-website-blue.svg)](https://donggun-jung.github.io/multiagent-protocol/)
[![한국어](https://img.shields.io/badge/lang-한국어-orange.svg)](README.ko.md)

> **v1.0.0.** The cron orchestrator is **live**: a fork evaluates open PRs, merges the auto-approvable quadrants (A/B/C), routes irreversible+critical changes (D) to you via the Decision Inbox, and audits `main` (L2 + L5). Hardened through multiple rounds of independent external review (including a Quadrant-D approval bypass found and closed). A few capabilities are intentionally **post-1.0** — automatic revert-PR creation, the automatic 60-day L4 burn-in (promote manually via `severity_overrides` today), and a Korean mirror of the concept docs — see [`STATUS.md`](STATUS.md).

---

## What problem does this solve?

If you are a single developer using two or more AI coding agents to work on the same GitHub repository, you have probably hit at least one of these:

1. **Concurrent edits collide.** Agent A is editing `app/auth.py` while Agent B (on a different machine, different model) is editing the same file. Nobody noticed; you spend an hour reconciling.
2. **An agent silently merges something irreversible.** A model "fixed a flaky test" by deleting the test. The commit was already on `main` before you saw it.
3. **You cannot tell who did what.** Three commits land overnight, signed `Claude` / `Codex` / `Cursor`, but you cannot see which session was responsible or what reasoning each used.
4. **GitHub Free has no branch protection on private repos.** You want "required status checks" + "merge only via bot" but cannot pay for GitHub Pro, or do not want to make the repo public.

`multiagent-protocol` is a **portable, vendor-neutral, self-built equivalent of branch protection** that runs entirely from your own GitHub account on the Free tier. It enforces:

- **Pre-merge gate (L1)** — 5 conditions a PR must satisfy before it can merge (label, CI green, owner approval or auto-approval per classifier, base up-to-date, identity trailers present). *Wired end-to-end in v0.2.0: C1 verifies the label was applied by an allowlisted actor (timeline), C3 owner-approval reads the live classifier verdict, and the cron loop merges / opens an inbox issue / comments accordingly.*
- **Post-merge re-validation (L2)** — required checks rerun on the merged commit; infrastructure-only failures (cancelled / never-run) are ignored. *Implemented as detection + incident in v0.2.0; automatic revert-PR creation is a documented follow-up (see [`STATUS.md`](STATUS.md)).*
- **Race-guard (L3)** — re-check the PR's base against `main` HEAD just before merge; the `merge_pr` call passes a `sha` precondition GitHub enforces server-side, and a stale base triggers an `update-branch` rebase. *Implemented.*
- **Identity gate (L4)** — validate every commit's `Agent-*` / `Task-Ref` trailers against your registry. *Trailer-format (C5) is implemented; registry tool/model lookup ships **advisory (P2)** in v0.2.0 — promote to a hard block via `severity_overrides`. The 60-day burn-in is later.*
- **Break-glass auditor (L5)** — scan `main` for `[break-glass-*]` commits and require an ADR within 24 hours. *Implemented, including the 24-hour ADR deadline check (v0.2.0).*

The protocol is split into a small bot (Python, ~3 kLOC, plug-in extensible) and a doctrine layer (Markdown files that an agent reads at session start). The bot runs as a GitHub App on a 5-minute cron — on GitHub Actions Free tier for small repos, or on a self-hosted runner for larger workloads.

## Why "multiagent" rather than "Claude" / "Codex" / etc.?

The protocol is intentionally vendor-neutral. It treats every agent as one of:

- `claude-code`, `codex`, `cursor`, `gemini-cli`, `aider`, `<anything else you register>`

Identity is enforced through commit trailers, not API endpoints, so adding a new agent vendor takes a one-line registry update (`config/agent_registry.yml`). No agent has special status; all are equally untrusted-by-default.

## What this is NOT

- **Not a CI/CD system.** Bring your own tests; the bot reads CI status from GitHub.
- **Not a code reviewer.** It routes PRs to *you* or to an auto-approval classifier; it does not opine on diff quality.
- **Not a replacement for branch protection on GitHub Pro.** If you can pay, GitHub's built-in protection is simpler. This is for people on the Free tier or with reasons to self-build.
- **Not a multi-tenant SaaS.** Each owner runs their own copy. No accounts, no servers (the optional web wizard is a static site that runs in your browser).

## Framework vs. your config

This repo is the **framework** — shared, public, generic. Your **config**
(identity, repo list, agent registry, custom skills) is a separate **private**
data layer under `config/`. The product = framework + your config; there is no
"public version" and "my version" of the *code*, only different config. The web
wizard generates your config layer for you. See
[`docs/concepts/configuration-model.md`](docs/concepts/configuration-model.md).

## Quick start (15 minutes)

The fastest path is the **web wizard**:

1. Open [https://donggun-jung.github.io/multiagent-protocol/wizard/](https://donggun-jung.github.io/multiagent-protocol/wizard/) in your browser.
2. Fill in: your GitHub login, the repos you want supervised, your preferred runner tier, and which built-in skills to enable.
3. The wizard generates 5 YAML config files (`owner.yml`, `projects.yml`, `env.yml`, `skills.yml`, `agent_registry.yml`) + a 1-click GitHub App registration URL.
4. Download the `.zip`, drop it into your fork of this repo, register the App, set 2 Actions secrets, push.

Or skip the wizard and read [`docs/guide/quick-start.md`](docs/guide/quick-start.md) for the manual path.

## Architecture (one paragraph)

The bot is **4 modules** (deliberately not 5-layer — the layers map 1-to-1 but `pr_validator.py` consolidates L1 + L3 + L4, `branch_supervisor.py` consolidates L2 + L5). State lives in GitHub (PR objects, Issues for Decision Inbox, repo files for canonical doctrine). The bot itself is stateless across cron ticks. Decisions you must make (Quadrant D: irreversible + critical) reach you as `decision:pending-owner`-labelled Issues; everything else (Quadrants A/B/C) the classifier auto-approves.

See [`docs/concepts/architecture.md`](docs/concepts/architecture.md) for the full design.

## Status

- **v1.0.0** (current): first stable release. L1–L5 enforced end-to-end, Decision Inbox open + poll/resolve, distribution pipeline (Docker/Action live, PyPI on tag), 167 tests. Hardened through multiple rounds of independent external review.
- **Post-1.0**: automatic revert-PR creation, automatic 60-day L4 burn-in, Korean mirror of the concept docs, multi-account installations (see [`STATUS.md`](STATUS.md)).
- **Maintenance**: best-effort, no SLA. See [`MAINTAINERS.md`](MAINTAINERS.md).

## Documentation

- [`docs/guide/quick-start.md`](docs/guide/quick-start.md) — 15-minute setup
- [`docs/concepts/architecture.md`](docs/concepts/architecture.md) — how the bot is organized
- [`docs/concepts/configuration-model.md`](docs/concepts/configuration-model.md) — framework (public) vs. your config (private): one codebase, two data layers
- [`docs/concepts/four-quadrants.md`](docs/concepts/four-quadrants.md) — the autonomy classifier (when does the bot decide vs. ask you)
- [`docs/concepts/five-tier-files.md`](docs/concepts/five-tier-files.md) — how to organize a repo for AI agents
- [`docs/guide/skills.md`](docs/guide/skills.md) — writing your own validators
- [`docs/concepts/break-glass.md`](docs/concepts/break-glass.md) — the doctrine for overriding the bot
- [`docs/guide/break-glass.md`](docs/guide/break-glass.md) — the step-by-step "how to" version
- [`docs/guide/multi-repo.md`](docs/guide/multi-repo.md) — supervising more than one repository
- [`docs/guide/self-hosted-runner.md`](docs/guide/self-hosted-runner.md) — moving the cron off GitHub Actions
- [Korean mirror](docs/ko/) — 한국어 미러

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Pull requests welcome; this project follows its own protocol (eat your own dog food).

## Security

Found a vulnerability? See [`SECURITY.md`](SECURITY.md) for responsible-disclosure.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

---

*This project is inspired by — and learns from the mistakes of — a private predecessor that hardcoded one owner's identity, VPS, and projects. The lessons survived; the personal data did not.*
