# Agent Instructions

You are an AI coding agent working on **multiagent-protocol** — the very repository whose protocol you are implementing. This is the dog-fooding repo: the rules in `docs/concepts/*.md` apply to you while you edit this codebase.

## Read this before editing

Pick the lane that matches what you will do this session:

### Lane 1 — read-only (≤ 10 min)

For: skim, audit, "what is this protocol?"

1. This file (to "Lane 1 done" below).
2. [`README.md`](README.md) — what the project is and is not.
3. [`docs/concepts/architecture.md`](docs/concepts/architecture.md) — the 4-module bot design.
4. [`docs/concepts/four-quadrants.md`](docs/concepts/four-quadrants.md) — the autonomy classifier.

You now understand the project. You do **not** yet have edit rights.

### Lane 2 — first mutation (≤ 30 min)

For: a focused change (typo, doc fix, single-file feature).

Lane 1 + add:

5. [`docs/concepts/five-tier-files.md`](docs/concepts/five-tier-files.md) — how the repo is organized.
6. [`docs/concepts/break-glass.md`](docs/concepts/break-glass.md) — when bypassing the bot is allowed.
7. [`CONTRIBUTING.md`](CONTRIBUTING.md) — workflow + commit trailer format.
8. The file(s) you intend to edit, end to end.

### Lane 3 — changes to `src/multiagent_protocol/` or `schemas/` (≤ 60 min)

For: bot code changes, schema changes, anything an agent cannot self-validate.

Lane 2 + add:

9. [`docs/concepts/decision-inbox.md`](docs/concepts/decision-inbox.md) — how Quadrant D reaches the human owner.
10. [`docs/concepts/skills-plugin.md`](docs/concepts/skills-plugin.md) — the plugin interface.
11. [`docs/concepts/mirror-cascade.md`](docs/concepts/mirror-cascade.md) — how canonical files propagate to adopter repos.
12. [`SECURITY.md`](SECURITY.md) — the threat model your change might affect.
13. Existing tests for any file you edit (`tests/test_<module>.py`).

## Non-negotiables

The following rules apply to every change, every session, every agent:

1. **No hardcoded personal data.** Never commit a real GitHub login, email address, machine name, VPS hostname, IP, or SSH alias. Use placeholders (`<your-github-login>`, `${OWNER_LOGIN}`, `example.com`, `192.168.1.1`). The CI scan in `.github/scripts/scan_no_personal_data.py` enforces this. Personal data belongs in the git-ignored `config/` layer (see [`docs/concepts/configuration-model.md`](docs/concepts/configuration-model.md)), never in tracked framework files.

2. **No hallucinated APIs or files.** If you reference a function, file, or doc, it must exist. If you propose adding one, explicitly mark it `(to be added)` and either add it in the same PR or open a follow-up Issue.

3. **No silent doctrine changes.** If your change modifies a rule in `docs/concepts/*.md` or `docs/guide/*.md`, the diff must show the rule's old form, new form, and a one-line "why" in the PR description.

4. **No skipping the bot on `main`.** Even if you have direct push rights to `main`, do not use them except via the documented break-glass flow (`[break-glass-*]` commit prefix + ADR within 24h).

5. **Commit trailers required.** Every commit you author must include:
   ```
   Agent-Tool: <claude-code|codex|cursor|gemini-cli|aider|manual|github-actions>
   Agent-Model: <model identifier or "n/a">
   Agent-Session: s_<4-16 lowercase alphanumeric/hyphen; alphanumeric bounds>
   Agent-Machine: <your-machine-handle>
   Task-Ref: <Issue#N|issue#N|PR#N|none|round-N/topic>
   ```

6. **No telemetry, analytics, or callbacks home.** The protocol must run entirely from the operator's GitHub + (optionally) their own VPS. No third-party services. No "let us know how you use it" pings.

7. **Bot self-supervision.** The bot does not gate its own PRs (chicken-and-egg). Bot-repo changes use `[break-glass-bot-self-update]` + ADR-within-24h. See [`docs/concepts/break-glass.md`](docs/concepts/break-glass.md).

## Common pitfalls

- **Writing code before reading concepts.** The four-quadrant classifier and the L1-L5 layers have specific names and meanings; renaming them mid-PR creates doctrine ↔ code drift. Read first.
- **Inventing identifiers that do not exist in this repo.** Round numbers (`R7`, `R14`), ADR numbers (`ADR 0009`, `ADR 0017`), or rule references (`BOT_SELF_SUPERVISION Rule 4`) that are not defined in `docs/concepts/*.md` or `docs/decisions/*.md` here are not valid context — they leaked from someone's earlier private project and have no meaning in this repository. If you need to cite a rule, cite a section that actually exists in this repo.
- **Adding any contributor's GitHub login, email, machine handle, VPS hostname, or other personally-identifying string as a "canary" or example.** The CI scan in `.github/scripts/scan_no_personal_data.py` flags public IPs, email addresses, and SSH-style host aliases; placeholders should use `<your-github-login>`, `you@example.com`, `192.168.1.1`. Real identifiers do not belong anywhere in this repo, including in scan-pattern lists or test fixtures.
- **Coupling to a specific runner platform.** The bot must work on GitHub Actions Free tier OR a self-hosted runner OR paid cloud. Tests must not assume any of the three.

## When you are blocked

If a required gate refuses to clear (CI fails, classifier rejects, schema mismatch), **stop and report `BLOCKED`** rather than working around the rule. The bot's enforcement is the whole point; bypassing it silently is a doctrine violation, not a clever fix.
