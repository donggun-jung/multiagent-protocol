# General preferences (built-in defaults)

`multiagent-protocol` ships with a set of opinions that apply by default to every installation. They are encoded as **built-in skills with `severity = P0`** (blocking) or as classifier rules that are always loaded.

These are the things the protocol believes are true for every solo developer using AI agents, regardless of their specific stack. If you disagree, you can override per-rule in `config/skills.yml`, but the override is opt-out — the defaults assume you want them.

This document lists each default, the rationale, and how to disable it.

## 1. No hallucinated references in commits

**Built-in**: `hook_hallucination_guard.py` (BranchHook, P0)

A commit's message body is scanned for `\`<file-path>\`` references. If a referenced file does not exist in the repo at the merged SHA, the commit triggers a `decision:hallucination-detected` Issue.

**Why**: AI agents frequently reference imaginary files in commit messages ("see `src/auth/legacy.py`" when no such file exists, or never existed). These references survive into the project's history and confuse later agents (and humans) who go looking.

**How to disable**: in `config/skills.yml`:

```yaml
disabled:
  - hook_hallucination_guard
```

Disabling is appropriate for prototype repos or repos with intentionally-placeholder paths. For a project intended to outlive a few weeks, keep it enabled.

## 2. No personal data in source code

**Built-in**: `.github/workflows/tests.yml` job `no-personal-data` (runs CI scan via `scripts/scan_no_personal_data.py`)

Source files under `src/`, `tests/`, `schemas/`, `.github/workflows/`, top-level `*.py|*.yml|*.toml` are scanned for:

- Email addresses (`@example.com` and `@example.org` are allowed; other domains fail).
- Public IPv4 addresses (private ranges allowed).
- SSH-style `Host <alias>` literals.

**Why**: Personal data leaks through example code more often than people realize. A `donggun-jung` left in a fork's example becomes a real attack surface (someone could spear-phish that email) once the fork is public. The protocol refuses to ship its own embarrassment.

**How to disable**: edit `.github/workflows/tests.yml` to drop the `no-personal-data` job. The scan script (`scripts/scan_no_personal_data.py`) is itself a regular file; you can also edit its patterns. But the default is "on" for every fresh fork.

## 3. Agent-* commit trailers required

**Built-in**: `validator_trailers.py` (Validator, P0)

Every commit in a PR must have these trailers, well-formed:

- `Agent-Tool: <one of agent_registry.tools>`
- `Agent-Model: <model id or n/a>`
- `Agent-Session: s_[a-z0-9-]{2,14}[a-z0-9]`
- `Agent-Machine: <handle>`
- `Task-Ref: <Issue#N|PR#N|none|round-X/topic|bot/topic>`

**Why**: Without these, you cannot tell which agent / model / session / machine authored a commit. When two agents step on each other, you need to be able to read git log and reconstruct who did what. This is the **most basic forensic capability**; the protocol treats it as non-negotiable.

**How to disable**: not permitted via `disabled:`. You can lower severity via `severity_overrides: validator_trailers: P2` (warn but don't block), but this is strongly discouraged — agents that don't write trailers will not write them voluntarily once the gate stops failing.

## 4. Empty PR is Quadrant D

**Built-in**: `classifier_empty_pr.py` (ClassifierRule)

A PR with zero file changes votes Quadrant D in the classifier.

**Why**: An empty PR with `ready-to-merge` is suspicious — either a bot bug, a race condition, or an attacker probing your gate. Forcing owner review on empty PRs is a cheap defense.

**How to disable**: 

```yaml
disabled:
  - classifier_empty_pr
```

Disabling is appropriate if you have a workflow that creates intentionally-empty marker PRs for releases. Most operators do not.

## 5. Bot's own repo PRs are Quadrant D

**Built-in**: `classifier_bot_self_repo.py` (ClassifierRule)

A PR whose target repository equals the bot's own repository votes Quadrant D.

**Why**: The bot cannot gate its own PRs without chicken-and-egg. Forcing them to Quadrant D ensures the owner sees them — the only path to merge is `[break-glass-bot-self-update]` direct push + ADR-within-24h, or owner-approved manual merge via the App's owner-deploy credential (planned).

**How to disable**: not permitted. This is structural.

## 6. classifier-judgment must be published by a canonical actor

**Built-in**: `validator_classifier_publisher.py` (Validator, P0)

When the bot reads a `classifier-judgment` check-run from a PR, it verifies `app.slug == config.classifier.publisher_slug` (default: `github-actions`). Mismatched slug → fail-closed (treated as classifier missing → Quadrant D default).

**Why**: Without this, **anyone** who can run a GitHub Actions workflow in **any repo** could publish a check-run named `classifier-judgment` with summary `Quadrant: A`. The bot's auto-approval path would honor it. The publisher-identity gate closes this attack.

**How to disable**: not permitted. Change `config.classifier.publisher_slug` if you publish classifier-judgment from a different App (advanced); the gate itself stays on.

## 7. Mirror cascade is detection, not auto-fix

**Built-in**: `drift_check.py` (module)

When the bot detects that a canonical path in a supervised repo diverges from the governance repo, it **opens an Issue** (`decision:mirror-drift-incident`). It does not automatically open a cascade PR.

**Why**: Auto-cascade is itself a Quadrant D operation (it modifies critical files in adopters without owner approval at the time). Until ADR R-N+1 explicitly authorizes auto-cascade, detection-only is the safe default.

**How to enable auto-cascade** (when implemented): future versions will add `config.drift_check.auto_cascade: true`. Not in v1.0.

## 8. Break-glass requires ADR within 24 hours

**Built-in**: `hook_break_glass_audit.py` (BranchHook, P0)

A commit on `main` whose subject starts with `[break-glass-*]` triggers L5 audit: actor allowlist check + ADR-within-24h existence check. Missing ADR opens `decision:break-glass-unaudited` Issue.

**Why**: Break-glass is intentionally costly. The ADR documents what you did and why — the cost discourages casual use, the audit trail keeps the project's reasoning intact across time. See [`docs/concepts/break-glass.md`](break-glass.md).

**How to disable**: not permitted via `disabled:`. You can extend the 24h window via `config.break_glass.adr_deadline_hours` (default 24), but the audit hook always runs.

## 9. Decision Inbox issues track owner reactions only

**Built-in**: `decision_inbox.py` only counts reactions/comments from actors in `config.owner.allowlisted_actors`.

**Why**: Without this, a teammate or an attacker could approve Quadrant D PRs the owner did not see. The allowlist ensures only authorized actors can route around the owner.

**How to extend**: add additional GitHub logins to `config.owner.allowlisted_actors`. The list is authoritative; no implicit additions.

## 10. No network calls from user skills

**Built-in**: skills loader rejects user skills that `import requests`, `import urllib`, `import socket`, etc.

**Why**: Skills run with full bot privileges. A skill making an external API call is a data exfiltration vector — the operator's GitHub token, PR content, and bot state are all in-process. By making the rule "no network from user skills," skills become pure functions of context.

**How to extend with network**: see [`docs/guide/skills.md`](../guide/skills.md) § "When you cannot do it with a skill". The right answer is to extend `PRContext` (a core change with ADR) rather than punch network through a skill.

## Severity override table

For any built-in skill, you may override severity in `config/skills.yml`:

```yaml
severity_overrides:
  validator_trailers: P2          # Warn, do not block. Strongly discouraged.
  hook_hallucination_guard: P3    # Audit only, no Issue opened.
  no_wip_markers: P0              # Promote a user skill from default P1 to P0.
```

Severity levels:

- `P0` — block immediately. Failing check fails the L1 gate.
- `P1` — block after 60-day burn-in window. New agents have a grace period.
- `P2` — warn only. Bot comments but the gate still passes.
- `P3` — audit only. Bot records in metrics but does not comment.

Lowering a built-in below its default severity is the operator's call; the protocol does not refuse, but the audit log records the override on every tick so the reasoning is auditable.

## How to add a general preference

If you believe a new opinion should be a built-in default for every installation:

1. Open an Issue tagged `proposal: general-preference`.
2. Argue: (a) every solo-dev installation will want this, (b) the cost of false-positive is low, (c) the cost of false-negative is high.
3. If consensus forms, write a Quadrant D PR adding the new built-in. The PR description must list the rationale; the ADR (filed in the same PR or as a follow-up) becomes part of `docs/decisions/`.

The bar for new general preferences is high. The current list is the result of one project's specific mistakes; new entries should be the result of new specific mistakes, not abstract concerns.
