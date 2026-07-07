# Quick start

There are two ways to install `multiagent-protocol`:

- **Delegated (recommended): your AI agent installs it for you.** Two ways in:
  run the [web wizard](../wizard/index.html) and paste its generated **agent
  prompt** — or skip even that and paste the **Interview-Mode bootstrap
  prompt** ([README § Quick start](../../README.md#quick-start), also in
  [`AGENT_SETUP.md`](../agent-setup/AGENT_SETUP.md)): your agent interviews
  you conversationally in your language, builds the config from your answers,
  then executes the runbook end to end. Either way you are needed for exactly
  two things (GitHub App clicks + the go-live confirmation). This path exists
  because the setup below, done by hand, takes a human roughly **1–2 hours** —
  an agent does it in minutes and verifies each step.
- **Manual: you do the steps yourself.** The rest of this page. Budget 1–2
  hours for a first install, not 15 minutes.

Either way you end with:

- A **private governance repo** holding your config and running the bot.
- A GitHub App that holds merge permission on your supervised repo(s).
- A cron workflow ticking at a cadence your Actions budget can afford.
- A Decision Inbox where irreversible/critical changes wait for you.

## What you need

- A GitHub account (Free tier works — read the cadence table in Step 3).
- A repository you want supervised (`<your-supervised-repo>` below).
- Local `git`, `gh` (authenticated as you), Python 3.10+.

## Step 1 — Create your private governance repo (mirror, NOT a fork)

Your governance repo carries your identity and repo list, so it must be
**private** — and GitHub cannot make a fork of a public repo private. Create
a **mirror** instead:

```bash
gh repo create <your-login>/multiagent-protocol-gov --private
git clone --bare https://github.com/donggun-jung/multiagent-protocol.git /tmp/map-mirror
git -C /tmp/map-mirror push --mirror https://github.com/<your-login>/multiagent-protocol-gov.git
rm -rf /tmp/map-mirror
git clone https://github.com/<your-login>/multiagent-protocol-gov.git
cd multiagent-protocol-gov
git remote add upstream https://github.com/donggun-jung/multiagent-protocol.git
```

Updates later: `git fetch upstream && git merge upstream/main` (there is no
"Sync fork" button for a mirror — this replaces it).

## Step 2 — Generate your config (web wizard, 6 files)

Open the wizard — hosted at
[https://donggun-jung.github.io/multiagent-protocol/wizard/](https://donggun-jung.github.io/multiagent-protocol/wizard/)
or locally from your mirror (`docs/wizard/index.html`). Everything happens in
your browser; nothing is transmitted anywhere.

It asks for your GitHub login, the repos to supervise, your runner tier, the
skills to enable — and **your working preferences** (language, report style,
how much your agents decide alone). It generates six files:

`owner.yml` · `projects.yml` · `env.yml` · `skills.yml` ·
`agent_registry.yml` · `preferences.yml`

Download the zip and commit it **in your private governance repo**:

```bash
unzip ~/Downloads/multiagent-protocol-config.zip -d .
git add -f config/          # -f: config/ is git-ignored upstream on purpose
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e . && python3 -m multiagent_protocol check-config
# check-config validates the five bot files; preferences.yml is agent-layer —
# validate it against its schema too:
python3 -c "import json,yaml,jsonschema;jsonschema.validate(yaml.safe_load(open('config/preferences.yml')),json.load(open('schemas/preferences.schema.json')));print('preferences OK')"
git commit -m "config: initial owner + projects + env + preferences" && git push
```

> ⚠️ Never commit `config/` to a public repo. The framework's CI
> (`no-config-in-public`) enforces this on the public upstream; your privacy
> in your own deployment comes from the governance repo being **private**.

## Step 3 — Deploy the cron workflow (and pick an honest cadence)

The framework ships its own `.github/workflows/bot-cron.yml` **dispatch-only**
(the public repo must not run a merge engine). Your deployment uses the wired
example:

```bash
cp deploy/bot-cron.example.yml .github/workflows/bot-cron.yml
```

Open the file and pick the `cron:` cadence. Honest arithmetic — one tick costs
~30–60 s of runner time:

| Cadence | Runner time/month | GitHub Free (2,000 min, private repos)? |
|---|---|---|
| `*/5` | ~72–144 h | No — self-hosted only ([guide](self-hosted-runner.md)) |
| `*/15` | ~24–48 h | Borderline |
| `*/30` (default) | ~12–24 h | Yes |
| hourly | ~6–12 h | Yes, slower reactions |

Commit and push. (GitHub `schedule` can lag at peak and is auto-disabled
after ~60 days of repo inactivity; the file keeps `workflow_dispatch` as the
manual backstop.)

## Step 4 — Create the GitHub App (3 min)

Open the registration URL the wizard gave you (GitHub's App Manifest flow —
if it fails in your browser, the wizard's **Manual fallback** section lists
the exact permission set for registering by hand at
*Settings → Developer settings → GitHub Apps*).

1. Click **Create GitHub App for me**.
2. **Install App** → **Only select repositories** → pick your governance repo
   **and** `<your-supervised-repo>`.
3. Copy the **App ID**; **Generate a private key** (a `.pem` downloads).

## Step 5 — Secrets (3 of them)

```bash
gh secret set MERGE_GATE_APP_ID      -R <your-login>/multiagent-protocol-gov --body "<app-id>"
gh secret set MERGE_GATE_PRIVATE_KEY -R <your-login>/multiagent-protocol-gov < ~/Downloads/<app>.pem
openssl rand -hex 32 | gh secret set MERGE_GATE_RECEIPT_KEY -R <your-login>/multiagent-protocol-gov
rm ~/Downloads/<app>.pem
```

`MERGE_GATE_RECEIPT_KEY` protects approval receipts and Decision-Inbox bodies
with an HMAC — without it, a leaked App token could forge approvals. Set all
three.

## Step 6 — First tick (observe mode)

The bot starts in **observe mode**: it classifies, comments, and audits, but
does **not** merge until you flip the switch (next step). Trigger a first run:

*Actions tab → `bot-cron` → Run workflow*, or
`gh workflow run bot-cron.yml -R <your-login>/multiagent-protocol-gov`.

The run should end green, with a `tick complete` line in the log. Missing
secrets or broken config fail loudly by design.

## Step 7 — Go live

When you are ready for the bot to actually merge:

```bash
gh variable set MERGE_GATE_MERGE_ENABLED -R <your-login>/multiagent-protocol-gov --body true
```

## Step 8 — Test with a sample PR

In `<your-supervised-repo>`: make sure the `ready-to-merge` label exists
(`gh label create ready-to-merge --color 0e8a16`), then:

1. Branch: `git checkout -b protocol-test`.
2. Commit a no-op change **with the five trailers**:
   ```
   test: verify bot evaluates PRs

   Agent-Tool: manual
   Agent-Model: n/a
   Agent-Session: s_quickstart-test
   Agent-Machine: localhost
   Task-Ref: none
   ```
3. Push, open a PR, apply `ready-to-merge`.

On the next tick the bot either **merges** (squash) or posts a **diagnostic
comment** listing exactly which conditions failed — fix those and it merges
on the following tick.

**If your repo has no CI at all:** the CI condition is fail-closed — zero
checks means no auto-merge, by design. Either add any minimal workflow to the
repo, or consciously opt out with `allow_no_ci: true` in `config/env.yml`.

## What's next

- **Teach your agents the rules** — install
  [`templates/adopter/`](../../templates/adopter/) (AGENTS.md + CLAUDE.md,
  with your preferences materialized) into each supervised repo. The
  delegated path does this as
  [AGENT_SETUP step 6](../agent-setup/AGENT_SETUP.md).
- **Multi-repo cascade** — [`docs/guide/multi-repo.md`](multi-repo.md).
- **Custom skills** — [`docs/guide/skills.md`](skills.md).
- **Self-hosted runner** — [`docs/guide/self-hosted-runner.md`](self-hosted-runner.md).
- **Break-glass** — [`docs/concepts/break-glass.md`](../concepts/break-glass.md).

## Troubleshooting

### The workflow does not run
- Secrets present? (`gh secret list`) — all three `MERGE_GATE_*`.
- Are you on the deployed workflow (Step 3) or still on upstream's
  dispatch-only file? The deployed one has a `schedule:` block.
- Scheduled runs lag at peak and die after ~60 days of inactivity —
  `gh workflow run` is the backstop.
- App installed on **both** repos (governance + supervised)?

### The bot comments but does not merge
- Is `MERGE_GATE_MERGE_ENABLED` set to `true` (Step 7)? Observe mode logs
  `observe-only: would have merged …` instead of merging.
- `ready-to-merge` label present, and applied by an **allowlisted** account
  (`config/owner.yml`)?
- All required checks green — or `allow_no_ci` consciously set?
- Base up to date with `main`? All five trailers well-formed? The diagnostic
  comment names the exact failing condition.

### "PEM private key" auth failure
- The secret must contain the **entire** PEM including the BEGIN/END lines.
- The key must be the RSA key GitHub generated for the App.

### The wizard cannot open the App-manifest URL
- Use the wizard's **Manual fallback**: it prints the full registration URL
  and the permission set for registering the App by hand.

## FAQ

**Do I need to keep my governance repo up to date with upstream?**
Periodically, yes: `git fetch upstream && git merge upstream/main`. Your
`config/` and your deployed workflow are yours; if the workflow file
conflicts, keep your version.

**Can I use this with a private supervised repo?** Yes — that is the primary
use case, and exactly what GitHub Free's missing branch protection leaves
unprotected.

**What happens if I uninstall the App?** The bot stops on the next tick.
Merging falls back to whatever branch protection your repo has (on Free +
private: none).

**Does this work on GitLab / Bitbucket / Codeberg?** Not yet — the API client
is GitHub-specific. Adapters are welcome, see `CONTRIBUTING.md`.
