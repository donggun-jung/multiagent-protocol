# Contributing

Thank you for your interest in `multiagent-protocol`. This is a small open-source project; contributions are welcome, but please read this short guide first.

## What contributions are welcome

- **Bug reports** with a reproducer (Issue or PR).
- **Documentation improvements** — typos, missing concepts, clearer wording. Especially welcome for the Korean mirror under `docs/ko/`.
- **New built-in skills** that are *generally applicable* (hallucination guards, secret scanners, etc. — not personal preferences).
- **Adapters for new AI agent vendors** (a one-line addition to `config/agent_registry.example.yml` plus tests).
- **Translation to additional languages** — see `docs/wizard/js/locales.js` (wizard UI strings) and `docs/{lang}/` (markdown docs).

## What contributions need a discussion first

- Architecture changes (the 4-module split, the 4-quadrant classifier, the 5-tier file taxonomy). Open an Issue tagged `design-question` before writing code.
- Adding dependencies. We try to stay small. `PyJWT`, `cryptography`, `requests`, `PyYAML`, `jsonschema` are the only runtime deps; a new one should justify its presence.
- Anything that requires running a backend server. The project is intentionally serverless (static GitHub Pages for the wizard, GitHub Actions / self-hosted runner for the bot).

## What we will not accept

- **Hardcoded personal data.** No usernames, emails, machine names, VPS hostnames, etc. — including in examples. Use placeholders (`<your-github-login>`, `${OWNER_LOGIN}`).
- **Privately-licensed code.** Apache 2.0 license requires us to refuse contributions under incompatible licenses (GPL, etc.).
- **Telemetry, analytics, "phone home" features.** The protocol does not report to anyone.
- **Agent-specific carve-outs.** No agent vendor (Claude, Codex, Cursor, …) gets special treatment in the core. All agents are equal subjects of L1-L5.

## Workflow

1. **Open an Issue first** if the change is non-trivial (more than ~50 LOC or any architectural change). This avoids wasted work.
2. **Fork** the repo, branch from `main`, make your change.
3. **Run tests locally**: `pip install -e ".[test]" && pytest`.
4. **Commit with Agent-* trailers** if you used an AI agent to author the change. The protocol enforces this on itself:
   ```
   Subject line in imperative mood

   Body paragraph explaining why.

   Agent-Tool: <claude-code|codex|cursor|gemini-cli|aider|...>
   Agent-Model: <model identifier>
   Agent-Session: s_<2-14 lowercase alphanumeric/hyphen><alphanumeric>
   Agent-Machine: <your-machine-handle>
   Task-Ref: <Issue#N | PR#N | none | round-X/<topic>>

   Co-Authored-By: <human reviewer> <email>
   ```
5. **Open a PR** against `main`. The bot will evaluate L1-L4 on your PR and either auto-merge (Quadrants A/B/C) or post a Decision Inbox issue (Quadrant D).

## Code style

- Python: `ruff` for linting, `mypy` for type checking. Run `ruff check . && mypy src/`.
- Markdown: prefer ATX-style headers (`#`), 100-char wrap, no trailing whitespace.
- YAML config: 2-space indent, lower_snake_case keys.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0 (same as the project). The Apache 2.0 ICLA is built into Section 5 of the license — no separate signing required.

## Maintenance

This project is maintained on a **best-effort, no-SLA basis** (see `MAINTAINERS.md`). Issues may go unanswered for weeks. PRs that are clean, focused, and include tests are most likely to land.

## Questions?

Open an Issue tagged `question`. For sensitive matters (security vulnerabilities, license disputes), see `SECURITY.md`.
