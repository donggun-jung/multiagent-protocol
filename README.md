# multiagent-protocol

**Self-built branch protection and decision routing for solo developers and small teams who use multiple AI coding agents (Claude Code, Codex, Cursor, Gemini-CLI, …) on the same GitHub repositories.**

> One human, many agents. Different sessions, different machines, different models — same `main` branch. This protocol stops them from stepping on each other, gates merges through a self-built check, and routes irreversible decisions back to you instead of letting agents decide.

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Status: public-alpha](https://img.shields.io/badge/status-public--alpha-orange.svg)](MAINTAINERS.md)
[![Docs](https://img.shields.io/badge/docs-website-blue.svg)](https://donggun-jung.github.io/multiagent-protocol/)
[![한국어](https://img.shields.io/badge/lang-한국어-orange.svg)](README.ko.md)

> ⚠️ **public-alpha — v0.0.2.** The doctrine, schemas, web wizard, and skills plugin contract are stable. The bot's cron orchestrator and several enforcers (24h ADR window, label-event sourcing, agent registry tool/model lookup) ship as **skeletons** in v0.x and are completed in v0.2.0. Read [`STATUS.md`](STATUS.md) for the canonical "what works today vs. what is planned" table before relying on this for production. We will not push features without flagging this header again.

---

## What problem does this solve?

If you are a single developer using two or more AI coding agents to work on the same GitHub repository, you have probably hit at least one of these:

1. **Concurrent edits collide.** Agent A is editing `app/auth.py` while Agent B (on a different machine, different model) is editing the same file. Nobody noticed; you spend an hour reconciling.
2. **An agent silently merges something irreversible.** A model "fixed a flaky test" by deleting the test. The commit was already on `main` before you saw it.
3. **You cannot tell who did what.** Three commits land overnight, signed `Claude` / `Codex` / `Cursor`, but you cannot see which session was responsible or what reasoning each used.
4. **GitHub Free has no branch protection on private repos.** You want "required status checks" + "merge only via bot" but cannot pay for GitHub Pro, or do not want to make the repo public.

`multiagent-protocol` is a **portable, vendor-neutral, self-built equivalent of branch protection** that runs entirely from your own GitHub account on the Free tier. It enforces:

- **Pre-merge gate (L1)** — 5 conditions a PR must satisfy before it can merge (label, CI green, owner approval or auto-approval per classifier, base up-to-date, identity trailers present). *Implemented in v0.0.2 as standalone evaluators (C1–C5). The L3 race-guard wraps the merge call. Owner-approval validator and label-event sourcing ship in v0.2.0.*
- **Post-merge re-validation (L2)** — same checks rerun on the merged commit; auto-revert if they fail post-merge but not for infrastructure-only failures. *L2 logic is **planned** for v0.2.0; `branch_supervisor.py` currently runs L5 only.*
- **Race-guard (L3)** — re-check the PR's base against `origin/main` HEAD just before merge; the bot's `merge_pr` call passes a `sha` precondition that GitHub enforces server-side. *Implemented.*
- **Identity gate (L4)** — validate every commit's `Agent-Tool`, `Agent-Model`, `Agent-Session`, `Agent-Machine`, `Task-Ref` trailers against your registry. *Trailer-format check is implemented; registry-based tool/model lookup is **planned** for v0.2.0.*
- **Break-glass auditor (L5)** — scan `main` for `[break-glass-*]` commits and require an ADR within 24 hours. *Implemented; the 24-hour deadline check ships in v0.2.0.*

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

- **v0.0.2** (current): **public-alpha**. Doctrine + schemas + wizard + plugin contract stable. Bot cron orchestrator + several enforcers ship as skeletons (see header banner + [`STATUS.md`](STATUS.md)).
- **v0.1.0** (next): doctrine ↔ code drift closed. All concept docs map 1-to-1 to implemented behaviour.
- **v0.2.0**: cron orchestrator complete. L2 + L4 registry + L5 24-hour deadline enforced.
- **v1.0.0**: PyPI release + GitHub Action + Docker image, after one external operator has run for 30 days.
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
