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

> **If your session dies mid-install — this runbook is resume-safe.** On entry
> *or re-entry*, do not assume a fresh install. Walk the steps in order and run
> each step's **Verify** block first: skip any step whose Verify already passes,
> and resume at the first one that fails. The steps are idempotence-checked — a
> Step 1 that finds the governance repo *already created*, or a Step 4 that
> finds the App *already registered*, is a **resume signal, not an error to
> retry** (blindly re-creating either is the one reliable way to make a real
> mess). No state file is needed: the deployed GitHub state — the repo, its
> secrets and `MERGE_GATE_MERGE_ENABLED` variable, the labels, the workflow, and
> the materialized agent kit — is your checklist.

**Inputs you need before step 0** — from the wizard zip, **or collected by
you in Interview Mode (next section)** if the operator prepared nothing:

| Input | Example |
|---|---|
| Operator's GitHub login | `<login>` |
| Repo(s) to supervise | `<login>/<project>` |
| The 6 config files | `owner / projects / env / skills / agent_registry / preferences` `.yml` |
| Runner tier decision | `actions-free` (default) or `self-hosted` |
| Where `gh` is authenticated | as the operator's account |

---

## Interview Mode — conversational install, no wizard required

If your operator arrives with nothing but this runbook (no wizard zip, no
config), **you conduct the setup interview yourself**, then continue to
step 0. Rules of the interview:

- **Speak the operator's language.** Detect it from how they talk to you;
  everything below is your script's *content*, not its wording.
- **One batched round.** Ask all questions in a single message, numbered,
  each with its default in brackets. Accept "전부 기본값으로" / "defaults
  are fine" as a complete answer. Follow up only on genuinely missing
  essentials (login, repo).
- **Offer defaults, never invent.** Unanswered optional fields are omitted
  from the YAML, not guessed.
- **Close the loop.** Before writing files, reflect a 5-line summary back
  ("I will supervise X for account Y, ticking every 30 min on GitHub Free,
  agents will speak Korean and bring you options-menus, quiet 23:00–08:00 —
  correct?") and wait for a yes.

The interview (map answers 1:1 onto the schemas in [`schemas/`](../../schemas/)
and the shapes in [`examples/solo-developer/config/`](../../examples/solo-developer/config/)):

1. **GitHub login** → `owner.yml github_login` (verify it matches `gh auth status`).
2. **Which repo(s) should be protected?** → `projects.yml supervised_repos`;
   governance repo will be `<login>/multiagent-protocol-gov` (step 1).
3. **Do those repos have CI (automated checks)?**
   - yes → ask which check names must pass → `env.yml required_checks`
   - no → offer the choice honestly: add a minimal sanity workflow
     (recommended; you will create it in step 5) or set
     `allow_no_ci: true` (gate merges without CI evidence — say that plainly).
4. **Runner** [GitHub Actions Free, ticking every 30 min]: fine for almost
   everyone starting out; 5-minute reactions need a self-hosted runner
   ([guide](../guide/self-hosted-runner.md)) → `env.yml runner_tier`.
5. **Working preferences** → `preferences.yml` (this is where the
   installation becomes *theirs*):
   - language your agents should use with them [their language]
   - written reports [same / english / bilingual]
   - report style [conclusion first / detailed]
   - decisions as [2–4 labeled options with pros+cons / plain questions]
   - collect questions and ask together? [yes]
   - autonomy [cautious = confirm outward/irreversible things ·
     standard = follow the quadrants · delegating = proceed and report]
   - quiet hours + timezone [none]
   - **taste ledger seeds**: "What do you keep repeating to your AI agents?
     Give me 1–5 one-liners; every agent will follow them from now on." [skip]
   - **vocabulary**: "Any nicknames or shorthand I should understand —
     project names, terms?" [skip]
6. **Skills** [defaults]: keep built-in defaults unless they ask.

Then: write the six files, validate exactly as step 2 does, show the
summary, get the yes — and run steps 0–9. At every **[HUMAN]** step, give
click-by-click directions in their language and wait; never rush a human
step.

**Bootstrap prompt** (this is all an operator needs to paste to their agent
to start a conversational install — the runbook it fetches is resume-safe, so
an interrupted or restarted session just re-reads it and continues from the
first step whose Verify fails, never from zero):

```text
You are my AI coding agent. Set up multiagent-protocol for me.
Fetch and follow: https://raw.githubusercontent.com/donggun-jung/multiagent-protocol/main/docs/agent-setup/AGENT_SETUP.md
I have not prepared any config. Start with the runbook's Interview Mode:
interview me in my own language (one batched round, offer defaults), build
the six config files from my answers, confirm the summary back to me, then
execute steps 0-9. Involve me only at the [HUMAN] steps, and when we reach
them, walk me through the clicks step by step.
```

---

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
log's first error, fix, and re-run this step. Then run
`python -m multiagent_protocol verify-setup` — every check must be PASS or an
expected SKIP (secrets can't be read via the App token; confirm the three
names with `gh secret list`). Details: [`VERIFY_SETUP.md`](VERIFY_SETUP.md).

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
tick. Finish with
`python -m multiagent_protocol verify-setup --e2e --login <login>` — under
`--e2e` a stale or missing tick hard-FAILs; clear every FAIL before treating
the installation as live.

### Second half — rehearse the stop (RECOMMENDED, skippable) [HUMAN]

The clean merge above was **the product moment #1**: good work flowing through.
This is **the product moment #2 — feel the stop**, the half you are actually
installing the gate for. It is safe to skip on a rushed install, but running it
once is how the operator learns to recognize and answer a real parked decision.
The plan: author one change the bot **must** route to the owner (Quadrant D —
irreversible + critical), watch the `decision:pending-owner` issue open, and
**reject** it — leaving `main` untouched.

**1 — Author an intentionally-critical PR.** In the same supervised repo, on a
new branch, add a throwaway workflow file whose only job is to touch the
always-Quadrant-D `.github/workflows/` path (the one critical path present in
essentially every repo):

```bash
git checkout -b gate-rehearsal-d
mkdir -p .github/workflows
cat > .github/workflows/zzz-gate-rehearsal.yml <<'YML'
# multiagent-protocol go-live rehearsal — throwaway, never merged.
# Exists only to touch the always-Quadrant-D .github/workflows/ path so the bot
# opens a real decision:pending-owner issue. Deleted at the end of this step.
on: workflow_dispatch     # manual-only: this file never runs on its own
jobs:
  noop:
    runs-on: ubuntu-latest
    steps:
      - run: echo "rehearsal only — this PR is meant to be rejected"
YML
git add .github/workflows/zzz-gate-rehearsal.yml
git commit        # carry all five Agent-* trailers, exactly as in the clean-merge run
git push -u origin gate-rehearsal-d
gh pr create --fill   # then apply the ready-to-merge label — same discipline as above
```

Do **not** rehearse D with a `src/` edit: the classifier reads that as Quadrant
**B** (reversible + critical) and **auto-merges it with no pause**
(`classifier_path_default.py`: `.github/workflows/` is in `ALWAYS_D_PREFIXES`
→ D, while `src/` is a plain `CRITICAL_PREFIXES` → B). Pushing a file under
`.github/workflows/` requires your `gh` token to carry the `workflow` scope —
Step 0 verified this.

**2 — Make the bot see it.** Dispatch a tick and watch it (Step 7's commands):
`gh workflow run bot-cron.yml -R <login>/multiagent-protocol-gov`, then the
`gh run watch` from Step 7. Don't wait for the schedule — dispatch manually
between every phase (the default `*/30` cadence is too slow to rehearse
against).

**Verify:** a `decision:pending-owner` issue is now open **in the governance
repo** (`<login>/multiagent-protocol-gov` → Issues) — **not** in the supervised
repo. This is the [HUMAN] hand-off point.

**3 — [HUMAN] Answer it — reject.** Walk the operator to that issue in the
**governance repo's** Issues tab and have them comment `/reject` (or react 👎)
on it. This is the stop you installed the gate for: nothing merged, and nothing
will, until the owner answers.

**4 — Let the verdict apply.** Dispatch a second tick (same `gh workflow run`
command).

**Verify:** the resolver **closed both the PR and the issue** — the issue is
labeled `decision:rejected` and the PR is closed with a `/reject` comment
(verdict `rejected`, action `closed-pr`). `main` in the supervised repo is
unchanged.

**5 — Clean up.** Delete the throwaway branch —
`git push origin --delete gate-rehearsal-d` (and drop the local branch). Because
the PR was rejected and never merged, the workflow file never reached `main`;
the repo is left exactly as it was before the rehearsal.

**Why reject, and not approve?** 👍 / `/approve A` would instead label the PR
`decision:approved-A` and merge it on the *next* tick (a 3-tick loop:
open → label → merge). But every Quadrant-D path is critical *by construction*,
so an approved throwaway would land on your real `main` — and then removing it
is itself another Quadrant-D change that re-opens the inbox. Rejecting leaves
`main` pristine in two ticks. That asymmetry — cheap to reject, deliberately
heavy to approve-then-undo — is the design, not an accident (see
[`docs/concepts/four-quadrants.md`](../concepts/four-quadrants.md) § "Why this
design").

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
6. **Session-start ritual:** run `python -m multiagent_protocol verify-setup`
   now and then (your agents do it too, per the kit); if `gate-liveness`
   reports the gate may be asleep, `gh workflow enable bot-cron.yml` brings it
   back before you rely on it.

---

## Step 10 — Offboarding (optional)

Removing the gate should be as disciplined as installing it. This step is keyed
to the install steps **in reverse**, so it doubles as cleanup for a *partial*
install: if you stopped at step N, start the teardown at the entry for step N
and work downward. Read both paths below before you start — the default
preserves your audit trail; the full purge destroys it.

**First, the reversible stop (not a teardown).** The instant way to make the bot
safe is the Step 8 switch — no removal required:

```bash
gh variable set MERGE_GATE_MERGE_ENABLED -R <login>/multiagent-protocol-gov --body false
```

The bot drops back to observe mode on the next tick (classifies and audits,
merges nothing). If you only want to **pause** the gate, stop here — flip the
variable back to `true` to resume. Everything below actually **removes** it.

**Discover, don't remember.** Enumerate what exists from the live source of
truth, never a saved manifest: `gh workflow list`, `gh secret list`,
`gh variable list` (each `-R <login>/multiagent-protocol-gov`),
`gh label list -R <login>/<project>` per supervised repo, and the App's
installation page. Read `config/projects.yml` `supervised_repos` for the repo
list.

### Default removal — preserve the audit trail

Undo the install from the top step down; each item has its own **Verify**.

**Stop and remove the tick (undo Step 3).** Disable, then optionally delete, the
deployed workflow:

```bash
gh workflow disable bot-cron.yml -R <login>/multiagent-protocol-gov
# optional — also delete the file from the governance repo:
git rm .github/workflows/bot-cron.yml && git commit -m "offboard: remove bot-cron" && git push
```

**Verify:** `gh workflow list -R <login>/multiagent-protocol-gov` no longer
shows `bot-cron` as active.

**Uninstall the GitHub App (undo Step 4). [HUMAN]** Only the operator can do
this. Walk them to *GitHub → Settings → Applications → Installed GitHub Apps →
(your app) → Configure → Uninstall* (or, to remove the registration entirely,
*Settings → Developer settings → GitHub Apps → (your app) → Delete*). The App ID
is on screen here — it is also the `MERGE_GATE_APP_ID` secret.

**Verify:** the App no longer appears on the installation page of the governance
repo or any supervised repo.

**Delete the three secrets (undo Step 4).**

```bash
for s in MERGE_GATE_APP_ID MERGE_GATE_PRIVATE_KEY MERGE_GATE_RECEIPT_KEY; do
  gh secret delete "$s" -R <login>/multiagent-protocol-gov || true
done
```

**Verify:** `gh secret list -R <login>/multiagent-protocol-gov` lists no
`MERGE_GATE_*` secret.

**Clean each supervised repo (undo Steps 5–6).** For every `<login>/<project>`
in `config/projects.yml` `supervised_repos`, remove the bot's labels — the
`decision:*` family plus `ready-to-merge` and `merge-gate-failure` — by
discovering the live set rather than hardcoding names:

```bash
gh label list -R <login>/<project> --limit 100 --json name -q '.[].name' \
  | grep -E '^(ready-to-merge|merge-gate-failure|decision:)' \
  | while read -r L; do gh label delete "$L" -R <login>/<project> --yes || true; done
```

Then, optionally: remove the agent kit (`AGENTS.md`, `CLAUDE.md`) if the repo has
no other use for it, and revert `allow_squash_merge` if you enabled it **only**
for the bot in Step 5
(`gh api -X PATCH repos/<login>/<project> -F allow_squash_merge=false`).

**Verify:** `gh label list -R <login>/<project>` shows none of the `decision:*`
or `ready-to-merge` labels.

**Keep the governance repo — deliberately (Steps 1–2 withheld).** Do **not**
delete the private governance repo in the default path. Its Decision-Inbox
issues, the L5 break-glass records, and the `bot-state` branch **are** your audit
trail — the receipts for every merge the gate allowed and every decision the
owner made. Discarding them silently would betray the same honesty the gate
exists to protect. Leave them; a dormant private repo costs nothing.

**What remains, and why that is good:** the governance repo, its issue history,
and the `bot-state` branch. The gate is *off* — App uninstalled, no tick, no
secrets — but the *record* of what it did survives, and the owner can read,
diff, or delete it later on their own terms. That is the intended end-state of a
clean, honest offboard.

### Full purge — destroys the audit trail

Only if the operator **explicitly** asks for everything, including history, to be
gone. This is irreversible — get an explicit yes before running it, and say
plainly what it destroys (the Decision-Inbox and break-glass records that are the
entire audit trail).

Do the Default-removal label/kit cleanup first (labels and the agent kit live in
the supervised repos, which survive a governance-repo delete), then delete the
governance repo:

```bash
gh repo delete <login>/multiagent-protocol-gov --yes
```

That single delete takes the workflow, all three `MERGE_GATE_*` secrets, the
`MERGE_GATE_MERGE_ENABLED` variable, the `bot-state` branch, **and** the entire
Decision-Inbox / incident-issue history with it.

**Verify:** `gh repo view <login>/multiagent-protocol-gov` returns *not found*.

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
| Answered the inbox issue but the next tick does nothing (Step 9 rehearsal) | Either your GitHub login is not in `config/owner.yml` `allowlisted_actors` — the resolver silently ignores reactions/comments from non-allowlisted accounts, so it never finds a verdict — or you answered on the issue in the **supervised** repo instead of the **governance** repo's Issues tab (that is where the `decision:pending-owner` issue lives). |

*This runbook is the executable counterpart of
[`docs/guide/quick-start.md`](../guide/quick-start.md). When they disagree,
this file is current — please open an issue on the discrepancy.*
