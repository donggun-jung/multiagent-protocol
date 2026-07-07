# AGENT_SETUP — the delegated installation runbook

**Audience: an AI agent.** If you are a human reading this: don't install by
hand — paste the wizard's "agent prompt" (or this file's URL) to your own AI
agent (Claude Code, Codex, or any capable CLI agent) and let it run this
runbook. You will be needed exactly twice, at the steps marked **[HUMAN]**.

**Audience contract (for the agent executing this):**

- Execute steps **in order, 0 → 9**. Every step ends with a **Verify** block —
  do not proceed on a failed verify. If the same verify fails twice, **stop
  and report the step number and the failing output** to your operator.
- Interrupt your operator **only** at **[HUMAN]** steps. Collect any other
  questions and ask them in one batch at the next [HUMAN] step (respect
  `preferences.communication.batch_questions`).
- Talk to your operator in **their language** (`preferences.language.primary`);
  keep commands and file contents verbatim.
- Never print, log, or commit secret values (App private key, receipt key).
  Never commit anything under `config/` to a **public** repo.

**Inputs you need before step 0** (from the wizard zip, or compose them
yourself per step 2):

| Input | Example |
|---|---|
| Operator's GitHub login | `<login>` |
| Repo(s) to supervise | `<login>/<project>` |
| The 6 config files | `owner / projects / env / skills / agent_registry / preferences` `.yml` |
| Runner tier decision | `actions-free` (default) or `self-hosted` |
| Where `gh` is authenticated | as the operator's account |

Terminology: the **governance repo** is the private copy of this framework
that holds the operator's config and runs the bot. We name it
`<login>/multiagent-protocol-gov` throughout.

---

## Step 0 — Preflight

```bash
gh auth status                  # must show the operator's login, with repo+workflow scopes
python3 --version               # 3.10+
git --version
```

**Verify:** all three commands succeed; `gh auth status` shows the intended
account. If `gh` is authenticated as a different account, stop — the gate's
label/approval checks are tied to the operator's identity.

## Step 1 — Create the private governance repo (mirror, NOT fork)

> Why not "Fork"? GitHub cannot make a fork of a public repo private, and the
> governance repo must be private because it will carry the operator's
> identity and repo list. A mirror gives you a private, disconnected copy
> with full history, plus an `upstream` remote for updates.

```bash
gh repo create <login>/multiagent-protocol-gov --private \
  --description "multiagent-protocol governance (private config layer)"

# Disable Actions BEFORE the mirror push: the mirror carries upstream's
# tags (v0.2.0, v0.9.9, ...) and pushing them would otherwise fire
# release.yml in YOUR repo (publishing a container image to your GHCR)
# plus a doomed Pages deploy and a test-matrix burst.
gh api -X PUT repos/<login>/multiagent-protocol-gov/actions/permissions \
  -F enabled=false

git clone --bare https://github.com/donggun-jung/multiagent-protocol.git /tmp/map-mirror
git -C /tmp/map-mirror push --mirror https://github.com/<login>/multiagent-protocol-gov.git
rm -rf /tmp/map-mirror

git clone https://github.com/<login>/multiagent-protocol-gov.git
cd multiagent-protocol-gov
git remote add upstream https://github.com/donggun-jung/multiagent-protocol.git

# Re-enable Actions now that the burst window is over, then switch off the
# inherited workflows a private deployment does not want (Pages deploy fails
# on a private Free repo; keep tests.yml only if your minutes allow).
gh api -X PUT repos/<login>/multiagent-protocol-gov/actions/permissions \
  -F enabled=true -F allowed_actions=all
gh workflow disable docs.yml -R <login>/multiagent-protocol-gov || true
```

**Verify:** `gh repo view <login>/multiagent-protocol-gov --json visibility
-q .visibility` prints `PRIVATE`; `git remote -v` shows `origin` (gov) and
`upstream` (framework); `gh api repos/<login>/multiagent-protocol-gov/branches/main -q .name`
prints `main` (the mirror actually arrived).

**Later upgrades** (replaces the fork "Sync" button):
`git fetch upstream && git merge upstream/main` — your `config/` and your
deployed workflow are yours; if `.github/workflows/bot-cron.yml` conflicts,
keep **your** version (step 3 explains why it differs from upstream's).

## Step 2 — Write and validate the config layer (6 files)

Place the wizard-generated files under `config/`. If the operator skipped the
wizard, compose the files yourself: start from
[`examples/solo-developer/config/`](../../examples/solo-developer/), follow the
schemas in [`schemas/`](../../schemas/), and interview the operator in **one
batched question set** (login, repos, CI status, runner tier, and the
preference fields of `schemas/preferences.schema.json`).

```bash
git add -f config/   # config/ is git-ignored upstream; -f is required and correct here
python3 -m venv .venv && . .venv/bin/activate   # avoids PEP 668 "externally managed" failures
python3 -m pip install -e . --quiet
python3 -m multiagent_protocol check-config
python3 - <<'EOF'
import json, yaml, jsonschema
jsonschema.validate(yaml.safe_load(open("config/preferences.yml")),
                    json.load(open("schemas/preferences.schema.json")))
print("preferences OK")
EOF
git commit -m "setup: initial config layer" && git push
```

(Add your own Agent-* trailers to every commit you make in this runbook —
the formats are in [`templates/adopter/AGENTS.md`](../../templates/adopter/AGENTS.md) §2.
Use `Task-Ref: none` during setup.)

**Verify:** `check-config` prints `config OK` with the operator's login,
governance repo `<login>/multiagent-protocol-gov`, and the supervised repos;
the preferences check prints `preferences OK`.

## Step 3 — Deploy the cron workflow

Upstream's `.github/workflows/bot-cron.yml` is **dispatch-only on purpose**
(the public framework repo must not run a merge engine). Your deployment
replaces it with the wired example:

```bash
cp deploy/bot-cron.example.yml .github/workflows/bot-cron.yml
```

Pick the cadence inside the file (`CRON:` marker) using honest arithmetic —
a tick costs roughly 30–60 s of runner time:

| Cadence | Runner minutes/month | Fits GitHub Free (2,000 min, private)? |
|---|---|---|
| `*/5`  | ~4,300–8,600 | **No** — self-hosted only |
| `*/15` | ~1,400–2,900 | Borderline — one repo, nothing else on Actions |
| `*/30` | ~720–1,440   | Yes (default for `actions-free`) |
| hourly | ~360–720     | Yes, with slower reaction time |

No edit is needed for the merge switch: the file already defaults to observe
mode via the `vars.MERGE_GATE_MERGE_ENABLED || 'false'` fallback — do NOT
hardcode `'false'` there, or step 8's variable flip will have no effect.
Commit and push.

> GitHub reality check: `schedule` triggers can lag minutes-to-tens-of-minutes
> at peak, and GitHub disables schedules after ~60 days without repo activity.
> The workflow file's `workflow_dispatch` trigger is the manual backstop; if
> you need reliable 5-minute ticks, read
> [`docs/guide/self-hosted-runner.md`](../guide/self-hosted-runner.md).

**Verify:** `gh workflow list -R <login>/multiagent-protocol-gov` shows
`bot-cron` as active, and the pushed file contains your chosen cron line.

## Step 4 — GitHub App + secrets **[HUMAN]**

The bot acts through a GitHub App the operator owns. Registration takes
clicks in the GitHub UI — this is one of the two human steps.

1. Build the registration URL: open the wizard's Step "GitHub App" (or run its
   logic — `docs/wizard/js/wizard.js`, `buildManifestUrl`) with the operator's
   values. Present the URL to the operator.
2. **[HUMAN]** Operator: open the URL → "Create GitHub App" → on the App page
   **Install App** → choose **Only select repositories** → select the
   governance repo **and every supervised repo** → then **Generate a private
   key** (a `.pem` downloads). Tell the agent the App ID (a number) and the
   `.pem` file's local path. *(If the manifest URL flow fails in your browser,
   register manually at Settings → Developer settings → GitHub Apps with the
   permission set listed in `deploy/bot-cron.example.yml`'s header comment.)*
3. Agent — store credentials as Actions secrets, generate the receipt key,
   then remove the PEM:

```bash
gh secret set MERGE_GATE_APP_ID    -R <login>/multiagent-protocol-gov --body "<app-id>"
gh secret set MERGE_GATE_PRIVATE_KEY -R <login>/multiagent-protocol-gov < "<path-to-pem>"
openssl rand -hex 32 | gh secret set MERGE_GATE_RECEIPT_KEY -R <login>/multiagent-protocol-gov
rm -P "<path-to-pem>" 2>/dev/null || rm "<path-to-pem>"
```

`MERGE_GATE_RECEIPT_KEY` is not optional in spirit: without it, approval
receipts and inbox bodies fall back to author-only integrity, meaning a leaked
App token could forge approvals. Set it now, never print it.

**Verify:** `gh secret list -R <login>/multiagent-protocol-gov` shows all
three `MERGE_GATE_*` secrets. The App's installation page lists governance +
all supervised repos.

## Step 5 — Prepare each supervised repo

For every `<login>/<project>` in `config/projects.yml`:

```bash
gh label create ready-to-merge -R <login>/<project> \
  --description "multiagent-protocol: done and verified — bot may merge" \
  --color 0e8a16 || true                                  # exists = fine
gh api repos/<login>/<project> --jq .allow_squash_merge   # bot merges via squash
# if false:
gh api -X PATCH repos/<login>/<project> -F allow_squash_merge=true
```

**CI decision** (batch this question if it wasn't answered in the inputs):
the gate's CI condition is fail-closed — a repo with **zero** checks never
auto-merges. Two honest options:

- **Recommended:** add a minimal sanity workflow to the supervised repo (an
  8-line "the tree parses" check beats no gate signal at all), or
- set `allow_no_ci: true` in `config/env.yml` (opt-in vacuous CI condition),
  and record that the operator chose it.

**Verify:** label exists, squash allowed, and either at least one workflow
exists in the supervised repo or `env.yml` carries `allow_no_ci: true`
(re-run `check-config` after env changes).

## Step 6 — Install the agent kit into each supervised repo

Copy [`templates/adopter/AGENTS.md`](../../templates/adopter/AGENTS.md) and
[`templates/adopter/CLAUDE.md`](../../templates/adopter/CLAUDE.md) to the
supervised repo root, filling every `{{PLACEHOLDER}}`
(table in [`templates/adopter/README.md`](../../templates/adopter/README.md)):

- `{{REPO_NAME}}`, `{{AGENT_TOOLS}}`, `{{MACHINE_HANDLE}}` — from config
- `{{TICK_MINUTES}}` — the cadence you chose in step 3
- `{{PREFERENCES_BLOCK}}` — render `config/preferences.yml` as a bullet list:
  language + report style + decision format + autonomy profile + quiet hours,
  then every `taste_ledger` rule (dated), then `vocabulary` as "term — meaning".

Commit on a branch, open a PR titled `setup: install multiagent-protocol
agent kit`, and merge it yourself — the gate is still in observe mode, and
this bootstrap PR is the last thing that merges without it. From step 8 on,
everything (including you) goes through the gate.

Re-run this step whenever `config/preferences.yml` changes — the materialized
block is a copy, and stale copies teach agents last month's preferences.

**Verify:** both files exist at the supervised repo root on `main` with zero
remaining `{{` markers.

## Step 7 — First tick (observe mode)

```bash
gh workflow run bot-cron.yml -R <login>/multiagent-protocol-gov
RUN_ID=$(gh run list -R <login>/multiagent-protocol-gov --workflow bot-cron.yml --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RUN_ID" -R <login>/multiagent-protocol-gov --exit-status
gh run view "$RUN_ID" -R <login>/multiagent-protocol-gov --log | grep -Ei "tick complete|supervised="
```

**Verify (two lines, exactly these shapes):** the watch exits 0 (run
`success`), and the grep shows BOTH a `config: … supervised=N …` line with
N ≥ 1 AND a `tick complete: {...}` line (a metrics dict — it does not repeat
the repo count; the `supervised=` line carries that). Missing secrets or
invalid config **fail loudly** here by design — if the run is red, read the
log's first error, fix, and re-run this step.

## Step 8 — Go-live **[HUMAN]**

**[HUMAN]** Operator: confirm you have seen the observe-mode tick and want the
bot to actually merge. This is the moment the gate becomes real.

```bash
gh variable set MERGE_GATE_MERGE_ENABLED -R <login>/multiagent-protocol-gov --body true
```

**Verify:** `gh variable list -R <login>/multiagent-protocol-gov` shows
`MERGE_GATE_MERGE_ENABLED = true`.

## Step 9 — End-to-end proof + handover

In one supervised repo, author a trivial change **the disciplined way**:
branch → commit carrying all five `Agent-*` trailers → PR → apply
`ready-to-merge`. Then either wait one cadence interval or dispatch a tick
manually (step 7 command).

**Verify (the product moment):** the bot merges the PR (squash) — or posts a
diagnostic comment listing exactly what it still wants. A diagnostic comment
is a *pass* for this test if fixing its items leads to a merge on the next
tick.

Close by giving the operator a short handover **in their language**:

1. **What is now true:** every merge to `main` in <repos> passes the gate;
   irreversible/critical changes stop and wait for you.
2. **Your two controls:** the `decision:pending-owner` issues (answer 👍 or
   `/approve` / `/reject`) — and the `ready-to-merge` label discipline your
   agents follow.
3. **Emergency stop:** set `MERGE_GATE_MERGE_ENABLED` to `false` (instant
   observe mode) or disable the `bot-cron` workflow.
4. **Upgrades:** `git fetch upstream && git merge upstream/main` in the
   governance repo (your config and workflow survive; conflicts on the
   workflow file: keep yours).
5. **Preferences:** edit `config/preferences.yml`, then re-run step 6 so your
   repos' agent kits pick it up.

---

## When something refuses to work

| Symptom | Likely cause → fix |
|---|---|
| Workflow never runs on schedule | GitHub cron lag (wait), or schedules auto-disabled after ~60 days of inactivity (re-enable in Actions tab), or you are still on upstream's dispatch-only file (redo step 3). Manual backstop: `gh workflow run`. |
| `Bad credentials` / PEM errors in the tick log | Secret must contain the **entire** PEM including BEGIN/END lines; the key must be the RSA key GitHub generated. Redo step 4.3. |
| PR blocked on C1 (label) | The label must be applied by an **allowlisted** account (`config/owner.yml`). Your `gh` identity applies labels — it must be the operator's login. |
| PR blocked on C2 (CI) | The supervised repo has zero completed checks (add the sanity workflow) or a required check failed. `allow_no_ci: true` only if the operator accepted that trade. |
| PR blocked on C5 (trailers) | One of the five trailers is missing or malformed — the diagnostic comment names which. Formats: `templates/adopter/AGENTS.md` §2. |
| Quadrant D issue opened for a routine change | The classifier read the paths as critical (rules/workflows/data). This is the designed pause — have the operator answer the issue rather than re-labeling. |
| Tick green but the log shows `supervised=0` | `config/projects.yml` not committed to the governance repo (step 2's `git add -f`), or the App isn't installed on the supervised repo (step 4.2). |

*This runbook is the executable counterpart of
[`docs/guide/quick-start.md`](../guide/quick-start.md). When they disagree,
this file is current — please open an issue on the discrepancy.*
