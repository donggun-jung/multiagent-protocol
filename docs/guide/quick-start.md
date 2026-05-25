# Quick start (15 minutes)

This guide gets `multiagent-protocol` installed on one of your repos in 15 minutes. You will have:

- A GitHub App that holds merge permission on `main`.
- A cron workflow that runs every 5 minutes.
- A Decision Inbox in your `multiagent-protocol` fork.
- One built-in skill ready to run.

You will NOT have (yet): self-hosted runner deployment, multi-repo cascade, custom skills. See `docs/guide/multi-repo.md` and `docs/guide/skills.md` for those.

## What you need

- A GitHub account (Free tier is fine).
- A repository you want to supervise. We will refer to it as `<your-supervised-repo>` below.
- 15 minutes.
- Local Python 3.10+ (optional — only if you want to test the bot before deploying).

## Step 1 — Fork the protocol repo (1 min)

Open the protocol repo on GitHub and click **Fork**. Your fork lives at `<your-github-login>/multiagent-protocol`. We will refer to this as `<your-protocol-fork>`.

The fork is the **governance repo** for your installation.

## Step 2 — Run the web wizard (5 min)

Open [`<your-protocol-fork>/docs/wizard/index.html`](../../docs/wizard/index.html) by:

- Browsing to `https://<your-github-login>.github.io/multiagent-protocol/wizard/` if you have GitHub Pages enabled, OR
- Downloading the fork and opening `docs/wizard/index.html` in your browser locally.

The wizard asks for:

1. **Your GitHub login** (so it can write back to your fork).
2. **The repos you want supervised.** Start with just one: `<your-supervised-repo>`.
3. **Runner tier**: pick "T1 — GitHub Actions Free" for the quick start.
4. **Skills to enable**: leave the defaults.

The wizard generates four files:

- `config/owner.yml`
- `config/projects.yml`
- `config/env.yml`
- `config/skills.yml`

It also generates a **1-click GitHub App registration URL**. Save it; you will use it in Step 4.

Click "Download config.zip" and unzip it into the root of your fork. Commit:

```bash
cd <your-protocol-fork>
unzip ~/Downloads/multiagent-protocol-config.zip
git add config/
git commit -m "config: initial owner + projects + env"
git push
```

## Step 3 — Create the GitHub App (3 min)

Open the URL the wizard gave you. It looks like:

```
https://github.com/settings/apps/new?manifest=<URL-encoded-manifest>
```

This is GitHub's App Manifest flow. It pre-fills the App's permissions, webhook settings, and description.

1. GitHub shows a review page. Click **Create GitHub App for me**.
2. On the next page, click **Install App** in the left sidebar.
3. Choose **Only select repositories** and pick:
   - `<your-protocol-fork>` (the governance repo)
   - `<your-supervised-repo>` (the repo you want gated)
4. Click **Install**.

On the App's settings page (`https://github.com/settings/apps/<your-app-name>`):

5. Copy the **App ID** (a number like `123456`). Save it.
6. Scroll to **Private keys**. Click **Generate a private key**. A `.pem` file downloads.

## Step 4 — Add Actions secrets (2 min)

In `<your-protocol-fork>` on GitHub:

1. Go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Add `MERGE_GATE_APP_ID` with the App ID from Step 3.5.
3. Add `MERGE_GATE_PRIVATE_KEY` with the entire contents of the `.pem` file (including `-----BEGIN/END RSA PRIVATE KEY-----` lines).

Delete the `.pem` file from your Downloads folder (it is no longer needed; GitHub stores the secret).

## Step 5 — Enable the bot (1 min)

In `<your-protocol-fork>`:

1. Go to **Actions** tab.
2. Find the **multiagent-protocol-cron** workflow. If it shows "Workflow disabled", click **Enable workflow**.
3. Click **Run workflow** → **Run workflow** to trigger the first run manually (so you do not have to wait for the cron).

Within ~30 seconds, the workflow logs should show:

```
[multiagent-protocol] cron tick start at <timestamp>
[multiagent-protocol] scanning 1 supervised repo(s): <your-supervised-repo>
[multiagent-protocol] tick complete: 0 PRs evaluated, 0 actions taken
```

The bot is now running.

## Step 6 — Test with a sample PR (3 min)

In `<your-supervised-repo>`:

1. Create a branch: `git checkout -b protocol-test`.
2. Add a no-op change: `echo "" >> README.md && git add README.md && git commit -m "test: verify bot evaluates PRs

Agent-Tool: manual
Agent-Model: n/a
Agent-Session: s_quickstart-test
Agent-Machine: localhost
Task-Ref: round-0/quick-start
"`.
3. Push and open a PR via the GitHub UI.

Within ~5 minutes, the bot will:

- Comment on the PR with the L1 evaluation result. Expect something like:
  ```
  Merge Gate L1 — merge blocked:
  - C1: ready-to-merge label not set
  Fix the items above and the bot will re-evaluate on the next cron tick.
  ```

Add the `ready-to-merge` label via the GitHub UI. Wait another ~5 minutes. The bot evaluates again; if your supervised repo has no other required CI checks, the bot should merge the PR.

## What's next

- **Multi-repo cascade** — supervise 2+ repos with canonical-file mirroring: [`docs/guide/multi-repo.md`](multi-repo.md).
- **Custom skills** — write your own validators: [`docs/guide/skills.md`](skills.md).
- **Self-hosted runner** — when GitHub Actions Free minutes run out: [`docs/guide/self-hosted-runner.md`](self-hosted-runner.md).
- **Break-glass** — what to do when the bot is broken: [`docs/concepts/break-glass.md`](../concepts/break-glass.md).

## Troubleshooting

### The workflow does not run

- Check `Settings → Secrets and variables → Actions` — both `MERGE_GATE_APP_ID` and `MERGE_GATE_PRIVATE_KEY` should be listed.
- Check `Settings → Actions → General → Workflow permissions` — the workflow needs `Read and write permissions` (or use the App's permissions, which is the default).
- Check the App is installed on **both** the protocol repo and the supervised repo, not just one.

### The bot comments but does not merge

- Verify the PR has the `ready-to-merge` label (C1).
- Verify all required checks are green (C2).
- Verify the PR base SHA is current with `main` (C4) — push a rebase if not.
- Verify the PR's commits have all 5 `Agent-*` trailers (C5).

### "PEM private key" auth failure

- The `MERGE_GATE_PRIVATE_KEY` secret must contain the **entire** PEM, including the header and footer lines. If you copied only the base64 body, it will fail.
- The PEM must be RSA, not Ed25519. GitHub Apps issue RSA keys by default; if you swapped to something else manually, swap back.

### The wizard says my browser cannot generate the App Manifest URL

- The wizard is JavaScript-only and runs in your browser. If the URL generation fails, copy the manifest JSON shown in the wizard's "Manual fallback" section and paste it into `https://github.com/settings/apps/new` manually.

## Frequently asked questions

**Do I need to keep the protocol fork up-to-date with upstream?** Yes — periodically. Use the GitHub UI's "Sync fork" button to pull upstream changes. The cascade workflow then propagates updated canonical files to your supervised repos.

**Can I use this with a private repo?** Yes — that is the primary use case. Set the App installation to "Only select repositories" and pick your private repo. The bot reads/writes via the App's token; no public visibility required.

**What happens if I uninstall the App?** The bot stops immediately on the next tick (no GitHub token). PRs in your supervised repo are no longer gated; merging falls back to whatever your repo's branch-protection (or lack of it) allows.

**Does this work on GitLab / Bitbucket / Codeberg?** Not yet. The bot's API client is GitHub-specific. Adding adapter support for another forge is an Issue-worthy proposal — see `CONTRIBUTING.md`.
