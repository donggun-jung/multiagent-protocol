# Changelog

All notable changes to this project will be documented in this file. The format adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/donggun-jung/multiagent-protocol/compare/v0.0.0...HEAD
