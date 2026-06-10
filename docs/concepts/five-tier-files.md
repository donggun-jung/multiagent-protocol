# Five-tier file taxonomy

`multiagent-protocol` works by giving every file in a supervised repository a **tier**. The tier determines who can edit it, who reads it at session start, and how it propagates between repos. Five tiers, no overlap.

The naming convention is descriptive (not the bot's internal name) so a new agent reading this document can map any path to a tier in seconds.

## The five tiers

| # | Tier name           | Lives in                         | Purpose                                                                  |
|---|---------------------|----------------------------------|--------------------------------------------------------------------------|
| 1 | Living knowledge    | `docs/context/`                  | Current state — product, vision, decisions, open questions. Updated freely. |
| 2 | Immutable records   | `docs/meetings/`, `docs/decisions/` | Append-only — meeting notes, ADRs. Never deleted, never edited after merge. |
| 3 | Operating doctrine  | `docs/concepts/`, `docs/guide/`  | Binding rules — the rules in this very document. Edited only via Quadrant D PRs. |
| 4 | Machine contracts   | `src/`, `schemas/`, `.github/`   | Code + workflows + schemas. Edited via standard PRs (classifier picks quadrant by file path). |
| 5 | Audit & receipts    | `bot-state/`, GitHub commit trailers, Actions artifacts | Auto-generated. Never edited by humans. |

The names are **stable across forks**. Adding a sixth tier requires updating this document via a Quadrant D PR.

## Tier 1 — Living knowledge (`docs/context/`)

Files an agent reads at session start to understand "where the project is right now". Updated as the project evolves; old states are not preserved (use git history for that).

Typical files:

- `docs/context/PRODUCT_CONTEXT.md` — what is being built, for whom, in one page.
- `docs/context/ROADMAP_AND_VISION.md` — where it is going, optimistically.
- `docs/context/DESIGN_DECISIONS.md` — a chronological index of accepted ADRs with one-line summaries.
- `docs/context/OPEN_QUESTIONS.md` — known unknowns; an agent should not assume an answer here.
- `docs/context/USER_FEEDBACK.md` — what real users say (if any).
- `docs/context/GLOSSARY.md` — terms used elsewhere in the repo.

**Edit policy**: any contributor can update via a regular PR. Classifier path-rule typically Quadrant A or B.

## Tier 2 — Immutable records (`docs/meetings/`, `docs/decisions/`)

The institutional memory. Once committed, never edited (except via revert + new file).

- `docs/meetings/YYYY-MM-DD-NN_<topic>.md` — meeting record. Schema: who, when, what was decided, what's deferred. One file per meeting.
- `docs/decisions/NNNN_<topic>.md` — ADR (Architecture Decision Record). Schema: context, decision, consequences, alternatives considered, status (Proposed / Accepted / Superseded by ADR-NNNN / Deprecated).

**Edit policy**: ADRs are amended only by writing a new ADR that supersedes the old one (with `Supersedes: ADR-NNNN`). Meeting records are amended only by a new meeting record (with `Supersedes: meeting-YYYY-MM-DD-NN`).

The bot enforces append-only behavior: a PR that deletes or modifies an existing meeting or ADR file is automatically Quadrant D.

## Tier 3 — Operating doctrine (`docs/concepts/`, `docs/guide/`)

The rules of the protocol itself. This is what an agent reads in Lane 2 or Lane 3 of `AGENTS.md`.

- `docs/concepts/*.md` — the rules (this file, `architecture.md`, `four-quadrants.md`, etc.). Read by agents to understand what they may and may not do.
- `docs/guide/*.md` — how to do common things (quick-start, write a skill, recover from a stuck bot, etc.). Read by agents and humans alike.

**Edit policy**: changes to `docs/concepts/*` are **always Quadrant D**. The classifier checks the path and assigns D regardless of the diff. This prevents an agent from quietly rewriting a rule that constrains it.

Changes to `docs/guide/*` are Quadrant B (auto-merge with audit) because they describe behavior, not enforce it. If a guide describes a wrong behavior, the next user will notice; if a concept document says a wrong rule, the bot will enforce the wrong rule.

## Tier 4 — Machine contracts (`src/`, `schemas/`, `.github/`)

Everything that has effect on a machine: code, JSON schemas, GitHub Actions workflows.

- `src/multiagent_protocol/` — bot source. Quadrant B (reversible + critical) for normal refactors; Quadrant D for changes to `pr_validator.py`'s merge logic, classifier, or auth.
- `schemas/*.json` — JSON Schema files. Changes to required fields → Quadrant D. Additive optional fields → Quadrant B.
- `.github/workflows/*.yml` — CI workflows. Quadrant D for changes that affect the gate's runtime (the workflow that runs `pr_validator.py`). Quadrant B for tests-only workflow.
- `pyproject.toml` — dependency manifest. Adding a runtime dep → Quadrant D. Adding a dev dep → Quadrant B.

**Edit policy**: classifier path-rule + file-content heuristics decide. See `docs/concepts/four-quadrants.md` § "Classifier rule composition".

## Tier 5 — Audit & receipts (`bot-state/`, trailers, artifacts)

Auto-generated, never edited by humans.

- `bot-state/branch_supervisor_watermarks.json` — the bot's per-repo scan watermarks, persisted by the App to a dedicated `bot-state` **branch** of the governance repo (never `main`, so the bot's own scanners can't self-trigger; see `architecture.md`). The only state the bot writes back.
- `bot-state/classifier_audit.jsonl` — append-only classifier decisions.
- Commit trailers (`Agent-Tool`, `Agent-Session`, etc.) — embedded in every commit message; the bot reads them, never writes them.
- GitHub Actions workflow artifacts — the per-tick `metrics_summary.json` upload, 90-day retention by default.

**Edit policy**: humans never edit. A PR that touches `bot-state/*` from a non-bot author is Quadrant D and the diff is auto-rejected unless the PR description explicitly says "manual bot-state correction" with reason.

## Why these particular tiers

The five-tier split solves five different problems with five different rules:

1. **Living knowledge** — solves "agents have stale context". By having a single directory the agent reads at session start, you can update one file and the next session sees it.
2. **Immutable records** — solves "agents rewrite history". An append-only meetings/ folder + ADRs preserves the reasoning trail that survives team turnover or LLM context resets.
3. **Operating doctrine** — solves "agents bypass the rules". By making `docs/concepts/*` Quadrant D, the bot prevents an agent from quietly weakening the rules that constrain it.
4. **Machine contracts** — solves "agents and humans diverge on what the code does". Code and schemas in one tier, edited via standard PR flow, classifier picks the right quadrant per change.
5. **Audit & receipts** — solves "we can't tell what the bot did". The audit log + commit trailers + workflow artifacts give you a complete forensic trail without needing the bot to be currently running.

If you find yourself wanting to add a sixth tier, you have probably found a different problem worth a new ADR — write one before touching this file.

## Adopting this taxonomy in your own repos

A fresh repo can adopt the five-tier taxonomy in 15 minutes:

```bash
mkdir -p docs/{context,meetings,decisions,concepts,guide} bot-state
touch docs/context/{PRODUCT_CONTEXT,ROADMAP_AND_VISION,DESIGN_DECISIONS,OPEN_QUESTIONS}.md
touch docs/concepts/MERGE_GATE.md  # your local doctrine
echo "{}" > bot-state/.gitkeep
git add . && git commit -m "Adopt multiagent-protocol five-tier taxonomy"
```

Then point `config/projects.yml` at this repo and the bot will pick up the structure. See [`docs/guide/quick-start.md`](../guide/quick-start.md).
