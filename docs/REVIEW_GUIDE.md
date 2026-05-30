# Reviewer guide — multiagent-protocol

> **Status:** this guide drove the pre-1.0 external review. That review is
> complete and its P0/P1 findings shipped as **v1.0.0**; the guide is kept as
> the historical record of what reviewers were asked to assess.

Thank you for reviewing. This is the entry point: it tells you what to assess,
in what order, and which open questions need your judgement. Target time: a
focused reviewer can cover the load-bearing parts in ~60–90 minutes.

## What this is (one paragraph)

A **self-built, vendor-neutral equivalent of branch protection** for a solo
developer (or small team) running multiple AI coding agents (Claude Code,
Codex, Cursor, Gemini-CLI, Aider, …) against the same GitHub repos on the Free
tier. A GitHub App on a 5-minute cron evaluates each open PR through a
5-condition pre-merge gate (L1), a race guard (L3) and an identity gate (L4);
auto-merges low-risk "quadrants" (A/B/C) and routes irreversible+critical
changes (D) to the owner via a Decision Inbox; and audits `main` for post-merge
regressions (L2) and break-glass commits (L5). See
[`README.md`](../README.md) and [`docs/concepts/architecture.md`](concepts/architecture.md).

## What changed since you may have last seen it

This is a **greenfield public re-creation** of a private predecessor. The
personal data (one owner's identity, VPS, repo list) is gone by design; the
doctrine and the security lessons survived. Since v0.2.0 the cron orchestrator
went from a skeleton to **live** — a fork now actually evaluates and merges PRs.
The v0.9.9 release candidate added the distribution pipeline (PyPI/Docker/Action);
after the review's fixes it shipped as v1.0.0.

## Suggested read order

1. [`README.md`](../README.md) — scope + the L1–L5 model.
2. [`docs/concepts/architecture.md`](concepts/architecture.md) — the 4-module bot.
3. [`docs/concepts/four-quadrants.md`](concepts/four-quadrants.md) — the autonomy classifier (who decides).
4. [`STATUS.md`](../STATUS.md) — what actually ships vs. what is deferred (the honesty matrix).
5. Code, in dependency order: `src/multiagent_protocol/runtime.py` (the per-PR
   decision engine), `main.py` (the orchestrator loop), `pr_validator.py`,
   `branch_supervisor.py` (L2/L5), `decision_inbox.py`, and the built-in skills
   under `skills/builtin/`.
6. [`SECURITY.md`](../SECURITY.md) — the threat model.

## What to assess (in priority order)

1. **Security — this is a merge gate; a bypass is critical.** Can a PR merge
   without a legitimate, current owner approval or a passing classifier verdict?
   Focus on `runtime.process_pr` (the only path to `merge_pr`), the C1 label-actor
   check, the C3 owner-approval logic, the Decision-Inbox tamper guard, and
   whether a user-supplied classifier skill can *lower* a quadrant.
   - **Known history (please re-check):** an earlier RC trusted a
     `decision:approved-*` label on presence alone — a Quadrant-D bypass (self-
     applied label; approval surviving a force-push). It was closed over two
     independent adversarial rounds: C3 now honours an approval label only if it
     was applied by the owner/bot **and** at/after the current head commit
     (fail-closed on unverifiable data), and the bot verifies its own App
     identity. See `tests/test_owner_approval_and_auto_revert.py` and the
     exploit regression tests in `tests/test_orchestrator.py`.
2. **Correctness.** PR routing (A/B/C → merge, D → inbox, blocked, race-rebased);
   L2 infra-vs-real failure differentiation + watermark advance; idempotent
   incident/issue opening across stateless ticks; robustness to partial GitHub
   payloads.
3. **Doctrine ↔ code alignment.** Does the code do what `docs/concepts/*` claim?
   `tests/test_doctrine_consistency.py` guards the obvious drift; deeper checks
   are welcome. Is anything in STATUS.md over-claimed?
4. **Usability.** Could a stranger deploy this from `docs/guide/quick-start.md` +
   the web wizard (`docs/wizard/`) without hidden steps? Is the framework /
   private-config split (`docs/concepts/configuration-model.md`) clear?
5. **Distribution + supply chain.** `action.yml`, `Dockerfile` (+ `.dockerignore`
   excludes `config/` so private config can't bake into an image), and
   `.github/workflows/release.yml` (OIDC trusted publishing, no stored token;
   GHCR via `GITHUB_TOKEN`). Are the pinned action SHAs and permissions right?

## 1.0 scope decisions — please rule on each (blocker vs post-1.0)

Listed in [`STATUS.md`](../STATUS.md) § "1.0 scope decisions". In short:

1. **L2 automatic revert-PR creation** (ships as detection + incident).
2. **L4 automatic 60-day burn-in** (ships advisory; manual promote via `severity_overrides`).
3. **Korean mirror of the concept docs** (README + quick-start are mirrored).
4. **Multi-account App installations** (single-account is supported).

For each: is it a 1.0 blocker, or acceptable as a documented post-1.0 item?

## Running it locally

```bash
python -m venv .venv && .venv/bin/pip install -e ".[test]" ruff
.venv/bin/pytest -q            # expect: 152 passed
.venv/bin/ruff check src/ tests/
python3 .github/scripts/scan_no_personal_data.py
```

The bot has no live integration tests against GitHub (it would need a real App);
the orchestrator is covered by an in-memory fake API (`tests/conftest.py`).
Independent adversarial tests against that fake are the most valuable thing you
can add if you suspect a bypass.

## Sending findings

Open issues on the repo, or return a findings list with: severity
(P0 blocker / P1 should-fix / P2 nice-to-have), `file:line`, the issue, and a
concrete suggested fix. The maintainer applies the fixes and ships 1.0.
