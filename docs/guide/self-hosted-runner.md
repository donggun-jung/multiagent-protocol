---
title: Self-hosted runner deployment
---

# Self-hosted runner: when GitHub Actions Free is not enough

GitHub Actions Free gives **2,000 minutes/month** on private repos. At the classic `*/5` cadence the bot-cron workflow burns roughly **4,300-8,600 minutes/month (72-144 hours)** — more than four times the entire free tier, regardless of repo count (one tick scans all supervised repos). That is why the Free-tier default cadence is `*/30` (~720-1,440 min/month); if you want 5-minute reactions, you need your own runner.

This guide explains how to move the cron tick onto your own always-on machine. After this, the per-tick Actions minutes consumed by your bot is **zero**.

> The bot's behaviour does not change between Actions-Free and self-hosted; only the runner host (the `runs-on:` selector) changes.

## Prerequisites

- A machine the bot can run on. Acceptable options:
  - A small VPS ($5-10/month — Hetzner CX22, DigitalOcean basic, etc.).
  - A Raspberry Pi 4 or newer on your home network.
  - An old laptop running Linux that you can leave on.
- Docker installed on the machine. The runner runs inside a container so it does not need a GitHub-runner-specific OS.
- 1-2 hours.

The protocol does **not** care which machine you pick. The doctrine applies equally to runners on Hetzner, your home Pi, or a colleague's spare server. None of them get special trust.

## Step 1 — Register a new GitHub Actions runner

On your governance repo on github.com:

1. **Settings → Actions → Runners → New self-hosted runner**.
2. Pick "Linux" / "x64" (or your arch).
3. GitHub gives you a config token. Copy it.

## Step 2 — Run the runner container

On your machine:

```bash
# Create a directory for the runner.
mkdir -p ~/multiagent-protocol-runner && cd ~/multiagent-protocol-runner

# Pull a maintained runner image. We do not publish our own — use upstream.
docker pull myoung34/github-runner:latest

# Start the runner.
docker run -d --restart=always \
  --name multiagent-protocol-runner \
  -e RUNNER_NAME="multiagent-protocol-runner" \
  -e RUNNER_TOKEN="<paste-the-token-from-step-1>" \
  -e RUNNER_WORKDIR="/tmp/runner/work" \
  -e RUNNER_SCOPE="repo" \
  -e REPO_URL="https://github.com/<your-github-login>/multiagent-protocol" \
  -e LABELS="self-hosted,multiagent-protocol" \
  --security-opt no-new-privileges \
  --tmpfs /tmp \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  myoung34/github-runner:latest
```

The `LABELS` value `self-hosted,multiagent-protocol` is how the bot-cron workflow finds this specific runner — see Step 3.

Verify it registered:

```bash
docker logs --tail 30 multiagent-protocol-runner
# Should show "Listening for Jobs"
```

In GitHub UI **Settings → Actions → Runners**, you should now see one runner with status "Idle".

## Step 3 — Point the bot-cron workflow at the runner

Edit `.github/workflows/bot-cron.yml` in your governance repo:

```yaml
jobs:
  tick:
    # Was: runs-on: ubuntu-latest
    runs-on: [self-hosted, multiagent-protocol]
    timeout-minutes: 5
    # ... rest unchanged
```

Commit + push. The next cron tick runs on your machine. GitHub Actions Free minutes consumed: **0**.

## Step 4 — Set `runner_tier: self-hosted` in env.yml

For honesty in `STATUS.md` and for `bot-cron` workflow conditionals:

```yaml
# config/env.yml
runner_tier: self-hosted
```

## Hardening checklist

A self-hosted runner is a machine that executes arbitrary code from your GitHub repos. Some sensible defaults:

- **Don't run on a host that has other production workloads.** If the runner is compromised, the blast radius is the whole host.
- **Resource limits.** Add `--memory 1g --cpus 0.5` to the `docker run` command if you do not want the runner to consume the whole machine during a bad cron tick.
- **No host network.** Don't run with `--network host`. The default bridge is fine and limits the runner's access to other services on the host.
- **PAT scope.** The `RUNNER_TOKEN` is short-lived (used only at registration). The long-lived credential the bot uses is the GitHub App's private key (in Actions secrets, not on the runner host). The runner itself does not need any GitHub credentials.
- **Image source.** `myoung34/github-runner` is a maintained third-party image. Pin to a digest (`@sha256:...`) rather than `:latest` once you're sure your setup works:
  {% raw %}
  ```
  docker pull myoung34/github-runner:latest
  IMG_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' myoung34/github-runner:latest)
  # Then update the docker run command to use $IMG_DIGEST instead of :latest.
  ```
  {% endraw %}
- **Updates.** The runner image is updated regularly (security fixes for the runner agent itself). Set a monthly calendar reminder to `docker pull` + restart the container.

## Multiple runners (for 6+ supervised repos)

If even one runner can't keep up with your fleet, you can run multiple containers. GitHub Actions distributes jobs to whichever runner is idle. The bot-cron workflow's `concurrency: bot-cron, cancel-in-progress: false` ensures only one tick runs at a time across all runners, so adding runners helps with workflow latency, not throughput.

Most operators do not need this. If you find yourself wanting more than one runner, the right question is "should I move to paid GitHub Actions instead?" — paid Actions removes the runner-management burden entirely.

## Backups

The runner is **stateless**. Each cron tick is a fresh container and `bot-state/` lives in the governance repo (committed by the bot after each tick). If your runner host dies, just spin up another container with the same `LABELS` and the cron resumes within 5 minutes.

The one thing **not** on the runner that matters is the GitHub App private key (it's in GitHub Actions secrets, not on the runner host). Keep a copy of the PEM somewhere safe (encrypted backup, password manager) so you can re-issue if you ever lose access to your GitHub account.

## Going back to GitHub Actions Free

If you want to undo this:

1. Revert `.github/workflows/bot-cron.yml` to `runs-on: ubuntu-latest`.
2. Revert `config/env.yml` to `runner_tier: actions-free`.
3. In GitHub UI **Settings → Actions → Runners**, remove the self-hosted runner.
4. Stop and remove the container: `docker stop multiagent-protocol-runner && docker rm multiagent-protocol-runner`.

The bot has no state on the runner; nothing else needs to be cleaned up.

## Next steps

- [`multi-repo.md`](multi-repo.md) — supervising more than one repo (the reason you wanted self-hosted in the first place).
- [`skills.md`](skills.md) — write custom validators that run inside the cron tick on your own machine.
- [`docs/concepts/architecture.md`](../concepts/architecture.md) — the design that this runner executes.
