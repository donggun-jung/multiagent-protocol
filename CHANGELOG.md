# Changelog

All notable changes to this project will be documented in this file. The format adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-07-07

**Fleet-parity additions + the delegated-installation release.** Four additive,
backward-compatible gate capabilities a running fleet depends on, plus the
**agent-first adoption path**: an external operator now installs the protocol by
handing one runbook to their own AI agent. Every new gate behavior is gated on
new **optional** config; with that config absent, the gate is byte-identical to
1.0.0 (the prior 168 tests are unchanged; suite now 435). This release also
repairs the documented install flow, which had drifted from the shipped tree
(dispatch-only workflow vs. promised cron, undocumented
`MERGE_GATE_MERGE_ENABLED`/`MERGE_GATE_RECEIPT_KEY`, an impossible
"private fork" instruction, and a ~12x Actions-minutes underestimate).

### Added

- **Delegated installation.** [`docs/agent-setup/AGENT_SETUP.md`](docs/agent-setup/AGENT_SETUP.md)
  — a step-0-to-9 runbook an operator's own AI agent executes end to end
  (private-mirror governance repo, config, workflow, App + secrets, labels,
  agent kit, observe tick, go-live, E2E proof). Human involvement: App
  registration clicks + go-live confirmation. Korean overview at
  `docs/ko/agent-setup/`.
- **Deployed-workflow example.** [`deploy/bot-cron.example.yml`](deploy/bot-cron.example.yml)
  — the workflow an installation actually runs: `schedule` + honest cadence
  table, `MERGE_GATE_MERGE_ENABLED` variable wiring, `MERGE_GATE_RECEIPT_KEY`,
  editable install from the mirror checkout (immune to the stale-wheel trap on
  reused runners). Upstream's own `bot-cron.yml` stays dispatch-only by design.
- **Operator preference layer.** `config/preferences.yml`
  (+ [`schemas/preferences.schema.json`](schemas/preferences.schema.json)):
  language, report style, decision format, autonomy profile, quiet hours, an
  append-only **taste ledger**, and personal vocabulary — read by the
  operator's agents, never by the bot; lives only in the private governance
  repo. Example in `examples/solo-developer/config/`.
- **Adopter agent kit.** [`templates/adopter/`](templates/adopter/) —
  AGENTS.md + CLAUDE.md for supervised repos: the five trailers with exact
  formats, label discipline, quadrant expectations, break-glass boundary, and
  a materialized operator-preferences block (placeholders filled by
  AGENT_SETUP step 6).
- **Wizard v2.** Preferences step (generates the 6th config file) and the
  agent prompt rewritten as a **delegation prompt** that drives AGENT_SETUP —
  no fork instruction anywhere.

- **R1 — named required checks.** A new optional `required_checks: [string]`:
  a global default in `env.yml` plus an optional per-repo override in
  `projects.yml` (`repo_overrides.<owner/name>.required_checks`). Per-repo wins,
  else global, else `[]` (= 1.0.0's "all completed checks succeed"). When
  non-empty, **each** named check must be present on the head SHA and conclude
  `success` — a missing required check **fails C2 fail-closed**, regardless of
  `allow_no_ci` (which only applies when the list is empty). Threaded through C2
  (`CiGreenValidator` per repo in `runtime.process_pr`) and L2 post-merge
  re-validation (`main.revalidate_main`, which previously always passed `()`).
- **R2 — read the published classifier verdict.** New built-in classifier rule
  `classifier_published_verdict`: reads the `classifier-judgment` check-run on
  the PR head, verifies the canonical publisher (`env.classifier_publisher_slug`,
  same identity gate as `validator_classifier_publisher`), parses a
  `Quadrant: X` (A/B/C/D) marker from its output/summary, and **votes** that
  quadrant into the max-vote engine. Because the engine takes the MAX, a
  published verdict can only **raise** (toward owner control), never lower.
  Absent / wrong-publisher / unparseable / ambiguous → **abstain** (votes the
  lowest quadrant, never raises an exception).
- **R3 — unauthorized-push detector** (the code-level alternative to paid branch
  protection). New built-in branch hook `hook_unauthorized_push`: over recent
  `main` commits, flags any commit whose committer is **not** the bot, whose
  subject is **not** a `[break-glass-*]` commit (those belong to the L5
  break-glass auditor), **and** whose committer login is **not** in the owner
  allowlist → opens a `decision:unauthorized-push` incident (idempotently, via
  the supervisor's existing per-SHA issue dedupe).
- **DEC-C — audit-only repo tier.** New optional `audit_only_repos: [name]` in
  `projects.yml`. An audit-only repo is **excluded** from the per-open-PR L1–L4
  gating loop but **included** in `branch_supervisor` (L2 post-merge + L5
  break-glass + the new R3 detector). Lets the governance repo be audited
  without the self-gating paradox. Default: no repo is audit-only.

### Hardened (cross-vendor GPT-5.5 adversarial review)

Five **fail-open** bypasses found in the v1.1 gate additions, each closed
fail-closed and additively (v1.0.0 defaults unchanged; no existing gate weakened):

- **R1 — duplicate check-name masking (C2).** `validator_ci_green` built a
  `{name: check}` map, so two runs named `build` (`failure` then `success`)
  collapsed to the success and **passed C2**. Now a required check is
  present-and-green only if it has ≥1 run **and no same-name run is non-success**
  (any failing/incomplete same-name run fails C2 fail-closed); the all-checks
  path inspects every run, never a name-deduped view, so a duplicate failing
  check can no longer be masked.
- **R2 — duplicate canonical verdict neutralization.** `classifier_published_verdict`
  abstained (voted A) when >1 `classifier-judgment` run was present, letting a
  real `Quadrant: D` be **neutralized** by a second canonical judgment. Now,
  among judgments published **by the canonical slug**, it takes the **MAXIMUM**
  quadrant (A<C<B<D) and never abstains-to-A on canonical duplicates; it still
  abstains when zero canonical judgments are present or all are unparseable.
  Non-canonical duplicates remain ignored (provenance is filtered first).
- **DEC-C — config as critical.** `classifier_path_default` `CRITICAL_PREFIXES`
  omitted `config/`, so a PR editing the gate's **own** config (adding a repo to
  `audit_only_repos`, changing `required_checks`, editing the agent registry)
  was not routed critical and could auto-merge as Quadrant A. `config/` is now a
  critical prefix → any config change is at least B/D (owner-visible).
- **R1 in L2.** `branch_supervisor.revalidate_main` returned `passed` under
  `allow_no_ci=True` even when a named `required_check` was **missing** on the
  merged commit. L2 now mirrors C2: a specified-and-missing (or non-success)
  required check is a **real failure** that opens the incident, regardless of
  `allow_no_ci` (which only relaxes the empty-required-list path).
- **R3 hardening.** (a) `hook_unauthorized_push` is now in the `NON_DISABLEABLE`
  core set, so the no-paid-branch-protection substitute cannot be silently
  turned off via `skills.disabled`. (b) The L5 break-glass actor allowlist check
  now uses the commit **committer** login, not the forgeable author
  (`git commit --author=…`), consistent with `hook_unauthorized_push`; a code
  comment + doctrine note record that commit committer metadata is
  association-not-push-actor (the true push actor needs the Enterprise audit-log
  API — a documented future item).

`docs/concepts/general-preferences.md` § 11 updated (the unauthorized-push hook
is no longer listed as disableable; committer-vs-author identity note added).

### Config + schema

- `schemas/env.schema.json`: optional `required_checks` (array of non-empty
  strings, unique).
- `schemas/projects.schema.json`: optional `audit_only_repos` (array of
  `owner/name`) and `repo_overrides` (map of `owner/name` →
  `{ required_checks: [...] }`, `additionalProperties:false`).
- `docs/concepts/general-preferences.md` § 6 (R2) and § 8/new § 11 (R3)
  documented; `docs/guide/multi-repo.md` gains a "Named required checks",
  "Audit-only repos", and "Published classifier verdict" subsection.

### Tests

- **59 new tests → 227 total** (config loader + schema acceptance, C2 + L2
  required-checks, the published-verdict rule incl. max-vote-only-raises and
  wrong-publisher abstention, the unauthorized-push hook incl. idempotency, and
  end-to-end `main()` audit-only gating) — **+14** from the cross-vendor
  hardening pass: C2 duplicate-name masking (fail-closed), R2 canonical-duplicate
  MAX (no neutralize-to-A), `config/` critical classification, L2 missing
  required check under `allow_no_ci`, `hook_unauthorized_push` stays armed when
  listed in `disabled`, and L5 committer-not-author actor trust. ruff clean; the
  168 prior tests pass unchanged.

## [1.0.0] - 2026-05-30 *(never tagged — first shipped inside v1.1.0)*

First **stable** release, after multiple rounds of independent external review
(Claude Opus, GPT-5-Codex). Resolves the review's P0/P1 findings. *Release
housekeeping note (2026-07-07): the v1.0.0 git tag was never cut, so this
scope reached external users for the first time with the v1.1.0 tag; the
compare link below reflects that.*

### Fixed (external-review P0/P1)
- **check-runs parser:** `github_api.check_runs` returned the
  `{total_count, check_runs}` envelope's KEYS instead of the check-run objects
  (the test fake served lists, hiding it) — this broke L1.C2 + L2 against live
  GitHub. Now extracts the array; + `test_github_api.py` (envelope, pagination,
  label_events, AppAuth.app_slug — previously 0 coverage).
- **`/approve C` now DEFERS** (doctrine: needs-more-info), not merge: labels
  `decision:deferred`, leaves the inbox issue open.
- **`decision_inbox.repository`** is honoured at runtime (was always governance_repo).
- **severity_overrides** can no longer downgrade a core L1 validator (C1–C5,
  publisher) below blocking; **skills.enabled** is now an allowlist for user
  skills (was parsed-but-dead).
- **L2 no-checks** respects `allow_no_ci` (was silently "passed"); a deployment
  fork with config/ but no secrets now exits **non-zero** (was a green no-op
  hiding a non-running gate).
- **GitHub Pages** renders (build source → Actions; landing links → `.html`).
- **`decision:auto-revert`** label is provenance-checked (owner/bot, at/after
  head) via the shared `label_provenance` helper that also backs C3.

### Changed
- Version → 1.0.0; packaging status → Production/Stable. `action.yml` usage now
  shows the required `actions/checkout`. Docs swept for stale version labels and
  L2/L4 over-claims.
- Korean README + quick-start refreshed to v1.0 parity (status badge, L2
  wording, 5-file config count, `git add -f`, framework-vs-config section).
- Wizard gained a **Manual fallback** section (copyable registration URL + raw
  manifest JSON) so a blocked pop-up / over-long URL no longer dead-ends the
  install. `multi-repo.md` seed loop fixed (real canonical paths, correct
  `mirror_paths.json` path, `$HOME` instead of an unexpanded `~`).
- `docs.yml` actions SHA-pinned (it holds `id-token`/`pages: write`). mypy
  reconciled from an unmet `strict = true` to an honest advisory config.

167 tests, ruff clean, personal-data scan clean.

## [0.9.9] - 2026-05-30

**1.0 release candidate.** Out for external review; after review fixes it ships as 1.0.

### Added
- **Distribution pipeline:** `Dockerfile` (+ `.dockerignore` that excludes `config/`
  so private config never bakes into a published image), a composite `action.yml`
  (`uses: donggun-jung/multiagent-protocol@v0.9.9`), and
  `.github/workflows/release.yml` — on a version tag it pushes a GHCR image (every
  `v*`) and publishes to PyPI via OIDC **trusted publishing** (gated to `v1.0.0+`;
  no API token stored in the repo).
- **`env.yml` `allow_no_ci`** (default false): opt-in so a repo with no CI can
  auto-merge (C2 passes vacuously). Default stays fail-closed.
- `docs/REVIEW_GUIDE.md` — entry point for external reviewers.

### Changed
- Version 0.2.0 → 0.9.9; packaging Development Status → Beta.
- README banner / STATUS / Status section reframed as the **1.0 release
  candidate**, with the explicit 1.0 scope decisions called out for reviewers.

### Carried-over security posture
The C3 owner-approval bypass found + closed in v0.2.0 (two independent adversarial
review rounds) stays covered by regression tests. **152 tests**, ruff clean,
personal-data scan clean.

## [0.2.0] - 2026-05-29

The cron orchestrator goes live: a fork now actually evaluates PRs, merges the
auto-approvable quadrants, routes Quadrant D to the owner, and audits `main` —
the behaviour the v0.0.x docs described as the target.

### Added

- **Orchestrator loop** (`main.py`): per installation → per supervised repo →
  per open PR runs L1/L3/L4 → merge (A/B/C) / Decision-Inbox issue (D) /
  diagnostic comment; then L5 break-glass + hallucination scan, L2 post-merge
  re-validation, mirror-drift check, and the Decision-Inbox poll. Incidents are
  opened idempotently, so the 5-minute tick is safe despite being stateless.
- **`runtime.py`** — assembles config-injected built-in skills (owner allowlist,
  publisher slug, agent registry, bot repo, ADR finder) + user skills; applies
  `skills.disabled` (with a non-disableable core set) and `severity_overrides`;
  runs the per-PR decision.
- **`__main__.py`** — `python -m multiagent_protocol` now works (the bot-cron
  invocation previously failed with no `__main__`).
- **L1.C1 actor check** — `ready-to-merge` is verified against the timeline
  (who applied it), not just presence (`github_api.label_events`).
- **L1.C3** — owner-approval validator wired with the live classifier verdict.
- **L2 post-merge re-validation** — re-runs required checks on merged `main`
  commits with infra-vs-real differentiation (`cancelled` / zero-duration =
  infra → retry; `skipped` = pass; real failure → incident).
- **L4 identity gate** — `validator_agent_registry` checks each commit's
  tool/model against `agent_registry.yml`; ships **advisory (P2)** per the
  burn-in doctrine (promote to P0 via `severity_overrides`).
- **Decision Inbox** — idempotent `open` + `resolve_open_issues` (poll owner
  reactions/comments, head-SHA tamper guard, apply `decision:approved-*` or
  close the PR on `/reject`).
- **Graceful no-op** — a tick with no `MERGE_GATE_*` secrets exits 0 with a
  clear log instead of failing (stops the public upstream's scheduled job from
  failing every 5 minutes).
- 30 new tests (orchestrator decisions, L2, L4, inbox resolution, runtime
  toggles, no-op) — **140 total**.

### Changed

- `pr_validator.evaluate_pr` is now severity-aware (P0/P1 block, P2 warn, P3 audit).
- `bot-cron.yml` actions pinned to commit SHAs (parity with `tests.yml`).
- New CI job `no-config-in-public` keeps personal config out of public repos.

### Deferred (documented, not shipped in 0.2.0)

- **L2 auto-revert PR creation**: L2 ships as detection + incident issue (with
  the `git revert` command). Having the bot author a revert PR in a supervised
  repo is itself a Quadrant-D action — a documented follow-up, like mirror
  auto-cascade.
- **L4 automatic 60-day burn-in** promotion: use `severity_overrides` to promote
  the registry gate to P0 manually for now.
- **Watermark commit-back** across runs: watermarks persist within a run + via
  the tick artifact; incident idempotency already prevents duplicate issues.
- **Multi-account installations** where governance and supervised repos live
  under different App installations are not yet handled (single-account is the
  supported shape).

## [0.0.2] - 2026-05-26

### Added

**Phase 0 — repo scaffolding**
- LICENSE (Apache 2.0), README (en + ko mirror), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, MAINTAINERS, CHANGELOG.
- pyproject.toml (Python 3.10+, deps: PyJWT, cryptography, requests, PyYAML, jsonschema).
- `.github/workflows/tests.yml` (pytest matrix py3.10-3.12 + ruff + no-personal-data CI scan).
- `.github/scripts/scan_no_personal_data.py` (heuristic guard for accidentally-committed personal data).
- AGENTS.md + CLAUDE.md (3-lane onboarding for AI agents working on this repo).
- `src/multiagent_protocol/` package skeleton.

**Phase 1 — architecture docs (English only initially)**
- `docs/concepts/architecture.md` — 4-module bot design.
- `docs/concepts/four-quadrants.md` — IR×CR axes, A/B/C/D verdicts, L4 burn-in.
- `docs/concepts/five-tier-files.md` — Living knowledge / Immutable records / Doctrine / Machine contracts / Audit & receipts.
- `docs/concepts/decision-inbox.md` — Quadrant D Issue schema, polling logic, head-SHA tamper detection.
- `docs/concepts/break-glass.md` — when/how/audit-trail.
- `docs/concepts/skills-plugin.md` — Validator/ClassifierRule/BranchHook Protocols, loader, security model.
- `docs/concepts/mirror-cascade.md` — canonical paths, drift detection, manual cascade workflow.
- `docs/concepts/general-preferences.md` — 10 built-in defaults including hallucination guard.
- `docs/guide/quick-start.md` + `docs/guide/skills.md`.

**Phase 2 — core implementation**
- `src/multiagent_protocol/types.py` — frozen dataclasses for PR / commit / file / check-run / labels / trailers.
- `src/multiagent_protocol/trailers.py` — git-style trailer parser (no shell-out).
- `src/multiagent_protocol/classifier.py` — max-quadrant verdict engine + JSONL audit log.
- `src/multiagent_protocol/skills/{base,loader}.py` — Protocol interfaces + loader with AST-based network-import refusal.
- `src/multiagent_protocol/skills/builtin/` — 5 validators (C1-C5 + classifier-publisher), 3 classifier rules (path / empty-PR / bot-self-repo), 2 branch hooks (hallucination + break-glass).
- `src/multiagent_protocol/auth.py` + `github_api.py` — GitHub App JWT + REST client with 5xx retry + TOCTOU-safe merge.
- 4 modules: `pr_validator.py`, `branch_supervisor.py`, `decision_inbox.py`, `drift_check.py`.
- `src/multiagent_protocol/config/loader.py` — YAML config loader with optional JSON Schema validation.
- `src/multiagent_protocol/main.py` + `cli.py` — cron entry + argparse CLI (`tick` / `init` / `check-config`).
- 54 pytest cases (trailers, classifier engine, all 5 validators, 3 classifier rules, skills loader).

**Phase 3 — config schemas + examples**
- `schemas/{owner,projects,env,skills,agent_registry}.schema.json` + `schemas/mirror_paths.json`.
- `examples/solo-developer/`, `examples/small-team/`, `examples/multi-domain/` — 3 progressive example configurations.
- `.github/workflows/bot-cron.yml` — */5 cron with concurrency group + artifact upload.

**Phase 4 — static web wizard**
- `docs/wizard/index.html` + `wizard.css` + `wizard.js` + `locales.js` — 7-step form, en/ko i18n, pure-client config generator with ZIP download (handwritten STORE-method ZIP, no jszip dep), GitHub App manifest URL builder, "agent-assist prompt" copy-paste output.

**Phase 5 — docs site + release prep**
- `docs/index.html` (English landing) + `docs/ko/index.html` (Korean landing).
- `docs/ko/guide/quick-start.md` (Korean mirror of quick-start).
- `.github/workflows/docs.yml` — GitHub Pages deploy on push to main.

### Notes

- The bot's per-repo processing loop in `main.py` is intentionally skeleton-only for v0.1; the integration-test scaffolding (VCR cassettes for GitHub API) lands in v0.2.
- Korean mirror covers the README landing + quick-start guide. Concept docs (architecture / four-quadrants / etc.) are English-only in v0.1; Korean mirror of concept docs is on the v1.1 roadmap.

[1.1.0]: https://github.com/donggun-jung/multiagent-protocol/compare/v0.9.9...v1.1.0
[1.0.0]: https://github.com/donggun-jung/multiagent-protocol/compare/v0.9.9...v1.1.0
[0.9.9]: https://github.com/donggun-jung/multiagent-protocol/compare/v0.2.0...v0.9.9
[0.2.0]: https://github.com/donggun-jung/multiagent-protocol/compare/v0.0.2-alpha...v0.2.0
[0.0.2]: https://github.com/donggun-jung/multiagent-protocol/releases/tag/v0.0.2-alpha
