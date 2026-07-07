---
title: Multi-repo cascade setup
---

# Multi-repo: supervising more than one repository

This guide assumes you have completed [`quick-start.md`](quick-start.md) for a single supervised repo and now want to add a second, third, or more. The protocol scales by **mirror cascade** — the governance repo holds canonical doctrine, and every supervised repo mirrors a small set of byte-identical files.

> **v1.0.0:** the cron orchestrator runs L1–L5 across all your supervised repos in a single tick (`for installation: for pr in list_open_prs: ...`) — install the App on each repo and they are all gated. Mirror cascade + drift are **detection-only** (the bot opens a drift Issue; auto-cascade PRs are post-1.0). See [`STATUS.md`](../../STATUS.md).

## Prerequisites

- An installation that already gates one repo per `quick-start.md`.
- A second repo you want gated. We will call it `<your-github-login>/repo-b`.

## Step 1 — Add the repo to `projects.yml`

In your governance repo, edit `config/projects.yml`:

```yaml
governance_repo: <your-github-login>/multiagent-protocol
supervised_repos:
  - <your-github-login>/repo-a            # already supervised
  - <your-github-login>/repo-b            # new
bot_repo: <your-github-login>/multiagent-protocol
```

Commit and push. The cron tick reads `projects.yml` on every invocation; no bot restart needed.

## Step 2 — Install the GitHub App on the new repo

On the App settings page (`https://github.com/settings/apps/<your-app-slug>/installations`):

1. Click **Configure** next to your account.
2. Under **Repository access**, switch from "Only select repositories" to either "All repositories" or add `repo-b` to the selection list.
3. Save.

The bot will pick up the new installation on the next tick.

## Step 3 — Decide which files cascade

The set of files that must be **byte-identical** across every supervised repo is `canonical_paths` in `schemas/mirror_paths.json` (in your governance repo). Defaults are conservative — point them at files that actually exist in your governance repo, since the seed loop in Step 4 copies each one:

```json
{
  "canonical_paths": [
    "schemas/agent_registry.schema.json",
    "schemas/skills.schema.json"
  ]
}
```

If you want stricter — say, every supervised repo must also contain a copy of `docs/concepts/four-quadrants.md` so adopter contributors see the same rules — add the path to `canonical_paths`. The next cron tick's `drift_check` will open a `decision:mirror-drift-incident` Issue if any adopter is missing the file.

If you want laxer — one adopter is allowed to diverge on a specific file — add a per-adopter exception:

```json
{
  "canonical_paths": [...],
  "exceptions": {
    "repo-b": [
      "schemas/skills.schema.json"
    ]
  }
}
```

`repo-b` may now diverge on `schemas/skills.schema.json`; every other adopter must still match.

## Step 4 — Initial seed

When you first add an adopter, it does not yet contain the canonical files. The bot will open a `decision:mirror-drift-incident` Issue on the next tick listing every missing path. To resolve:

```bash
# In the new adopter repo:
cd <your-workspace>/repo-b
git checkout -b setup/mirror-canonical-files

# Copy the canonical files from your governance repo.
# (Replace $HOME/repos/multiagent-protocol with your governance repo's path.)
for p in $(jq -r '.canonical_paths[]' "$HOME/repos/multiagent-protocol/schemas/mirror_paths.json"); do
  mkdir -p "$(dirname "$p")"
  cp "$HOME/repos/multiagent-protocol/$p" "$p"
done

git add .
git commit -m "Adopt multiagent-protocol canonical files (initial cascade)

Agent-Tool: manual
Agent-Model: n/a
Agent-Session: s_initial-cascade
Agent-Machine: <your-machine>
Task-Ref: none
"
git push -u origin setup/mirror-canonical-files
gh pr create --fill
```

Merge the PR. The next cron tick re-runs `drift_check` and the incident Issue auto-closes.

## Step 5 — Ongoing cascade

When you change a canonical file in your governance repo, every supervised repo must receive the update. The bot does **not** automatically open cascade PRs in adopters (this is intentional — auto-PRs into adopters would be a Quadrant D operation, and the default policy is detection-only). Instead, drift opens an Issue, and you cascade manually.

If you want to automate cascade PRs anyway:

1. Wait for a future release + an explicit ADR in `docs/decisions/` that authorizes the bot to open critical-path PRs in adopters. The ADR will define an opt-in `drift_check:` block in `config/projects.yml`; the schema does not yet contain it.
2. Or hand-roll a workflow in your governance repo that, on push to `main`, opens a PR in each adopter with the canonical files copied over. This is operator-specific and we do not ship a default template — the right design depends on whether your adopters share a common owner, who reviews cascade PRs, etc.

## Sizing notes

The number that matters is your **cron cadence**, not your repo count: one
tick scans *all* supervised repos in a single run, and tick duration grows
only mildly with each added repo.

| Cadence | Runner time/month (tick ≈ 30-60 s) | GitHub Free (2,000 min, private)? |
|---------|-------------------------------------|-----------------------------------|
| `*/5`   | ~4,300-8,600 min (72-144 h)         | No — self-hosted only             |
| `*/15`  | ~1,400-2,900 min                    | Borderline                        |
| `*/30`  | ~720-1,440 min                      | Yes (the `actions-free` default)  |
| hourly  | ~360-720 min                        | Yes, slower reactions             |

Many supervised repos with many open PRs stretch each tick longer (watch the
`metrics_summary.json` artifact); when ticks pass ~2 minutes or you want
5-minute reactions, move to a
[self-hosted runner](self-hosted-runner.md) — after that, Actions minutes
consumed by the bot drop to zero.

## Things to watch

- **Decision Inbox issue volume.** With more supervised repos, more Quadrant D PRs land in `<governance_repo>` Issues. If the inbox grows past ~20 open issues, audit your classifier rules — too many false-positive Ds usually means a path heuristic is mislabelled.
- **Cron tick duration.** Whatever cadence you chose assumes each tick finishes well inside the interval. With 6+ repos and ~10 open PRs per repo, ticks may run long. Use the workflow's `metrics_summary.json` artifact to track tick duration; if it exceeds 4 minutes consistently, either prune empty/abandoned PRs or move to self-hosted.
- **Cross-repo drift incidents.** A drift incident in repo A does not stop the bot from gating PRs in repo B. The incidents are independent.

## Per-repo configuration (v1.1)

Three optional knobs let one installation treat its supervised repos differently.
All default to "off", so omitting them keeps the v1.0.0 behavior.

### Named required CI checks (`required_checks`)

By default C2 (CI-green) passes only when **every** completed check-run on the
PR head is `success`. To instead require **specific named** checks — and fail
**closed** if one of them is missing — set `required_checks`. A global default
goes in `env.yml`; a per-repo override goes in `projects.yml` under
`repo_overrides` (per-repo wins; else global; else the v1.0.0 behavior):

```yaml
# env.yml — applies to every supervised repo unless overridden
required_checks:
  - lint
  - test
```

```yaml
# projects.yml — override just one repo
repo_overrides:
  <your-github-login>/repo-b:
    required_checks: [build, e2e]      # repo-b requires these instead
  <your-github-login>/repo-c:
    required_checks: []                # repo-c: back to "all checks succeed"
```

When `required_checks` is non-empty, each named check must be **present** on the
head SHA **and** conclude `success`; a missing one fails C2 regardless of
`allow_no_ci` (which only relaxes the empty-list case). The same effective list
is re-checked by L2 post-merge re-validation on `main`.

### Audit-only repos (`audit_only_repos`)

Mark a repo audit-only to **scan its `main`** (L2 post-merge + L5 break-glass +
the unauthorized-push detector) while **skipping L1–L4 PR gating** for it. This
is how you supervise the governance repo itself without the self-gating paradox
(the bot cannot gate the repo that holds its own doctrine):

```yaml
# projects.yml
supervised_repos:
  - <your-github-login>/multiagent-protocol   # governance repo
  - <your-github-login>/repo-a
audit_only_repos:
  - <your-github-login>/multiagent-protocol   # audited, but its PRs are not gated
```

### Published classifier verdict

If your fleet publishes a `classifier-judgment` check-run (from your own script)
carrying the authoritative quadrant as a `Quadrant: X` line in its summary, the
bot reads it and votes that quadrant — but only from the canonical publisher
(`env.yml` `classifier_publisher_slug`). Because the classifier takes the
maximum quadrant, a published verdict can only **raise** a PR toward owner
review, never lower it. No config beyond the publisher slug is required; repos
that publish no judgment are unaffected.

## Next steps

- [`self-hosted-runner.md`](self-hosted-runner.md) — when GitHub Actions Free is not enough.
- [`skills.md`](skills.md) — add per-repo custom validators via the plugin system.
- [`docs/concepts/mirror-cascade.md`](../concepts/mirror-cascade.md) — the design behind drift detection.
