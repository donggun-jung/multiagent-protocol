# Mirror cascade

When you run `multiagent-protocol` across more than one repository, some files must be **byte-identical** in every repo: the L1 validators (`pr_validator.py`), the schema files, the workflow `.yml` that runs the bot. If they diverge, the bot in repo A might accept what the bot in repo B rejects.

The mirror cascade is the protocol's answer: a designated set of canonical paths in the **governance repo** is the source of truth; every adopter repo's copy is checked against it on each cron tick. Drift is surfaced as an Issue (not auto-fixed — auto-fix would itself be a Quadrant D operation).

This document specifies the canonical-paths registry, the cascade workflow, and the drift-detection semantics.

## The governance repo

You designate one repository as **governance** in `config.governance.repository`. By convention this is the same repo that holds `docs/concepts/`, `docs/decisions/`, and the bot source if the bot lives in the protocol repo (versus a separate bot repo).

The governance repo is the single source of truth for canonical files. Adopter repos mirror the canonical paths.

## Canonical paths

A canonical path is a path listed in `<governance>/schemas/mirror_paths.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "schema_version": 1,
  "canonical_paths": [
    ".github/workflows/protocol_check.yml",
    "schemas/agent_registry.schema.json",
    "schemas/classifier_rules.schema.json",
    "src/multiagent_protocol/skills/builtin/validator_trailers.py",
    "docs/concepts/architecture.md",
    "docs/concepts/four-quadrants.md"
  ],
  "exceptions": {
    "<adopter-repo-name>": [
      ".github/workflows/protocol_check.yml"
    ]
  }
}
```

A path under `canonical_paths` must exist in every adopter repo at the **same path** and with **byte-identical content** as in the governance repo. Exceptions (per-adopter divergence is permitted) are listed in `exceptions[<repo-name>]`.

The set of canonical paths is small on purpose. Anything that does not need to be uniform should not be canonical — it is just code duplication.

## Drift detection

`drift_check.py` runs on each cron tick:

```python
for path in mirror_paths.canonical_paths:
    src_sha = governance_repo.compute_sha256(path)
    for adopter in supervised_repos:
        if path in mirror_paths.exceptions.get(adopter.name, []):
            continue
        try:
            adp_sha = adopter.compute_sha256(path)
        except FileNotFoundError:
            open_drift_incident(adopter, path, kind="missing")
            continue
        if adp_sha != src_sha:
            open_drift_incident(adopter, path, kind="differs",
                                src_sha=src_sha, adp_sha=adp_sha)
```

A drift incident opens a `decision:mirror-drift-incident` Issue in the governance repo (one issue per tick, listing all drifting paths). The Issue is **not** auto-closed when drift resolves — the operator closes it manually after pushing the cascade PR.

## Cascade workflow (manual)

When canonical content changes in the governance repo, the operator runs the cascade workflow to propagate it to adopters:

```bash
gh workflow run cascade.yml \
  --repo <governance-owner>/<governance-repo> \
  -f canonical_paths=schemas/agent_registry.schema.json,docs/concepts/architecture.md
```

The workflow:

1. Checks out the governance repo.
2. For each adopter listed in `config.projects.supervised`:
   a. Checks out adopter on `main`.
   b. Creates a branch `cascade/<governance-commit-sha-short>`.
   c. Copies the listed canonical paths from governance into adopter.
   d. Commits with subject `cascade: sync canonical paths from <governance-repo>@<sha-short>` and full `Agent-*` trailers (`Agent-Tool: github-actions`, `Agent-Session: s_cascade<8-hex>`, etc.).
   e. Opens a PR titled `cascade: <governance-sha-short> from <governance-repo>`.

The cascade PRs go through the normal L1 gate. They are typically Quadrant B (reversible + critical), and the bot merges them on the next tick if CI passes.

## Cascade workflow (planned: automatic)

A future version may open the cascade PRs automatically whenever a `decision:mirror-drift-incident` issue opens. This is on the R-N+1 candidate list because auto-cascade is itself a Quadrant D operation — the bot opening Quadrant-B PRs in adopters without the operator pressing a button is a significant trust delegation that needs its own ADR.

For now, the manual `gh workflow run` is the supported flow.

## What happens when an adopter diverges intentionally

Sometimes you want adopter A to use a customized classifier rules file while adopter B uses the canonical one. The mechanism is `mirror_paths.exceptions`:

```json
{
  "canonical_paths": [
    "schemas/classifier_rules.schema.json"
  ],
  "exceptions": {
    "adopter-A": [
      "schemas/classifier_rules.schema.json"
    ]
  }
}
```

Adopter A may now diverge on `schemas/classifier_rules.schema.json`. `drift_check.py` skips that path for adopter A.

This is intentionally per-path-per-adopter; you cannot blanket-except an entire adopter from the cascade. Every divergence is an explicit entry in the exceptions table.

## What does NOT participate in cascade

These intentionally do NOT cascade:

- **README.md and docs in adopters.** Each adopter is its own product; READMEs are product-specific.
- **Source code outside `src/multiagent_protocol/`.** The protocol's source is canonical; the adopter's app code is its own.
- **Tests.** The protocol's tests live in the protocol repo; adopters write their own.
- **Bot state files.** `bot-state/*.json` is generated, not canonical.
- **`config/owner.yml`, `config/projects.yml`, `config/env.yml`.** These are owner-specific; never canonical.

If you find yourself wanting to cascade something not in the above list, the right question is: "should this file be the same across all repos, including future ones I haven't created yet?" If yes, add it to `canonical_paths`. If no, leave it adopter-local.

## Why no automatic two-way sync?

Some protocols implement two-way file mirroring (cascade in either direction). `multiagent-protocol` does not, because:

- The governance repo is the **deliberate** source of truth. Promotion from adopter back to governance is a Quadrant D decision (it changes the rules) and should go through a normal PR-to-governance flow, not auto-sync.
- Two-way sync is much harder to make correct (which side wins on conflict?) and the value is small for the use case (solo operator with 1-10 supervised repos).
- One-way cascade keeps drift_check simple: governance is source, adopter is mirror, mismatch is drift.

If you want a protocol with two-way sync, you want a different tool (`renovate`, `dependabot`, `repo-sync`) — not this one.

## Cascade frequency in practice

For a solo operator with 4-6 adopter repos:

- Most weeks: 0 cascade PRs. The canonical paths do not change.
- When a doctrine update happens (you edited `docs/concepts/architecture.md`): 1 cascade PR per adopter, all opened in the same workflow run, all auto-merge as Quadrant B.
- When a schema breaks backward compatibility: cascade is preceded by a Quadrant D PR in governance, then cascade follows.

If you see drift incidents opening repeatedly without you running a cascade workflow, something is wrong — possibly an adopter is being edited directly by an agent that does not know about the cascade. Investigate before papering over with manual cascade.
