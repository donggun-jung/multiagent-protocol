---
title: Multi-repo cascade setup
---

# Multi-repo: supervising more than one repository

This guide assumes you have completed [`quick-start.md`](quick-start.md) for a single supervised repo and now want to add a second, third, or more. The protocol scales by **mirror cascade** — the governance repo holds canonical doctrine, and every supervised repo mirrors a small set of byte-identical files.

> **Status (v0.0.2):** the cron orchestrator that runs L1 across multiple repos in a single tick is a skeleton; see [`STATUS.md`](../../STATUS.md). The doctrine and drift-detection logic in this guide work today, but the full loop (`for installation: for pr in list_open_prs: ...`) lands in v0.2.0. You can still install on multiple repos now — the manifest cascade and drift Issue path work — but the bot will not yet comment on PRs in any of them.

## Prerequisites

- An installation that already gates one repo per `quick-start.md`.
- A second repo you want gated. We will call it `<your-github-login>/repo-b`.

## Step 1 — Add the repo to `projects.yml`

In your governance fork, edit `config/projects.yml`:

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

The set of files that must be **byte-identical** across every supervised repo is `governance/schemas/mirror_paths.json` `canonical_paths`. Defaults are conservative:

```json
{
  "canonical_paths": [
    ".github/workflows/protocol_check.yml",
    "schemas/agent_registry.schema.json",
    "schemas/classifier_rules.schema.json"
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
      "schemas/classifier_rules.schema.json"
    ]
  }
}
```

`repo-b` may now diverge on `classifier_rules.schema.json`; every other adopter must still match.

## Step 4 — Initial seed

When you first add an adopter, it does not yet contain the canonical files. The bot will open a `decision:mirror-drift-incident` Issue on the next tick listing every missing path. To resolve:

```bash
# In the new adopter repo:
cd <your-fork>/repo-b
git checkout -b setup/mirror-canonical-files

# Copy the canonical files from your governance fork.
# (Replace ~/repos/multiagent-protocol with your governance fork's path.)
for p in $(jq -r '.canonical_paths[]' ~/repos/multiagent-protocol/schemas/mirror_paths.json); do
  mkdir -p "$(dirname "$p")"
  cp "~/repos/multiagent-protocol/$p" "$p"
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

When you change a canonical file in your governance fork, every supervised repo must receive the update. The bot does **not** automatically open cascade PRs in adopters (this is intentional — auto-PRs into adopters would be a Quadrant D operation, and the default policy is detection-only). Instead, drift opens an Issue, and you cascade manually.

If you want to automate cascade PRs anyway:

1. Wait for v0.2.0 + an explicit ADR in `docs/decisions/` that authorizes the bot to open critical-path PRs in adopters. The ADR turns `config.drift_check.auto_cascade` into a supported flag.
2. Or hand-roll a workflow in your governance fork that, on push to `main`, opens a PR in each adopter with the canonical files copied over. This is operator-specific and we do not ship a default template — the right design depends on whether your adopters share a common owner, who reviews cascade PRs, etc.

## Sizing notes

| Supervised repos | Recommended runner tier                  |
|------------------|-------------------------------------------|
| 1-3              | `actions-free` (the default)              |
| 4-6              | `actions-free` works until late month; consider `self-hosted` |
| 7+               | `self-hosted` (see [`self-hosted-runner.md`](self-hosted-runner.md)) |

GitHub Actions Free tier gives **2,000 minutes/month** on private repos. A single bot-cron tick takes ~30-60 seconds × 12 ticks/hour × 24 hours/day × 30 days ≈ **6-9 hours/month per repo**, which exhausts the free tier around the 4-5 supervised-repo mark. The self-hosted-runner guide explains how to move the cron tick onto your own machine to escape this limit.

## Things to watch

- **Decision Inbox issue volume.** With more supervised repos, more Quadrant D PRs land in `<governance_repo>` Issues. If the inbox grows past ~20 open issues, audit your classifier rules — too many false-positive Ds usually means a path heuristic is mislabelled.
- **Cron tick duration.** The default 5-minute interval assumes each tick takes < 2 minutes. With 6+ repos and ~10 open PRs per repo, ticks may run long. Use the workflow's `metrics_summary.json` artifact to track tick duration; if it exceeds 4 minutes consistently, either prune empty/abandoned PRs or move to self-hosted.
- **Cross-repo drift incidents.** A drift incident in repo A does not stop the bot from gating PRs in repo B. The incidents are independent.

## Next steps

- [`self-hosted-runner.md`](self-hosted-runner.md) — when GitHub Actions Free is not enough.
- [`skills.md`](skills.md) — add per-repo custom validators via the plugin system.
- [`docs/concepts/mirror-cascade.md`](../concepts/mirror-cascade.md) — the design behind drift detection.
