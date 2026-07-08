# `verify-setup` — re-check your DEPLOYED gate

`check-config` validates the config files on disk. `verify-setup` goes one step
further: it re-checks the gate that is actually **deployed on GitHub** and
prints a **setup verification report** — App installation coverage, the workflow
file, the `ready-to-merge` label, squash merging, the bot-state branch, and —
the trust-critical one — whether the cron is still *ticking*.

It is **read-only** against GitHub and **serverless**: run it from your machine
or as an Actions job, using the same `MERGE_GATE_*` App credentials the tick
uses. It is re-runnable any time.

```bash
# Local (App creds in your env):
python -m multiagent_protocol verify-setup

# Assert YOUR login is allowlisted, and emit machine-readable JSON:
python -m multiagent_protocol verify-setup --login <your-github-login> --json

# Go-live / end-to-end mode: hard-fail a stale or missing tick (use right
# after dispatching one — see AGENT_SETUP Step 9):
python -m multiagent_protocol verify-setup --e2e
```

`--config-dir` / `--schemas-dir` default to `config` / `schemas`.

## Exit code and output

- **Exit `0` only when no check FAILs.** WARN / SKIP / INFO never fail the
  command; a missing CRITICAL artifact does.
- The report ends with one machine-readable STATUS line, e.g.
  `SETUP: OK — passed=11 failed=0 warnings=2 skipped=3 info=4`.
- `--json` emits `{ok, status, summary{passed,failed,warnings,skipped,info},
  checks[{id,status,detail}]}`.
- **The report names your repos and App slug.** Send it to stdout or an Actions
  artifact — **never commit it to the public upstream.** (The framework's
  personal-data CI scan guards the repo; the report is a private surface.)

## Before secrets are wired

Every GitHub-dependent check degrades to `SKIP (no credentials)` when
`MERGE_GATE_APP_ID` / `MERGE_GATE_PRIVATE_KEY` are absent, so the command is
still useful early: the local checks (config loads, no placeholders, allowlist,
merge-mode) run, and the GitHub checks skip cleanly instead of erroring.

## What each check means

| id | severity | what a non-PASS means |
|---|---|---|
| `config-loads` | CRITICAL | `config/*.yml` failed to load / validate |
| `preferences-schema` | FAIL / SKIP | `preferences.yml` present but fails its schema (SKIP if absent) |
| `config-placeholders` | CRITICAL | an unfilled placeholder (`your-…`, `{{TOKEN}}`, `<…-here>`) is still in your config |
| `allowlist-actors` | INFO / PASS-FAIL | with `--login`, asserts your login is allowlisted (the #1 C1 failure: the label-applier MUST be allowlisted); without it, echoes the list to eyeball |
| `agent-tools-declared` | INFO | vendor-neutral echo of the agent CLIs YOU declared in `agent_registry.yml` — install those CLIs where your agents run |
| `merge-mode` | INFO | OBSERVE (evaluates, does not merge) vs LIVE (`MERGE_GATE_MERGE_ENABLED=true`) — surfaced so you never think you are protected while silently in observe mode |
| `secrets-present` | SKIP | secret **values** are unreadable via the App token by design — confirm the three names yourself: `gh secret list -R <gov-repo>` |
| `app-auth` | CRITICAL / SKIP | the App did not authenticate (SKIP = no creds) |
| `app-installed` | CRITICAL | the App installation does not cover the governance repo + **every** supervised repo (the documented "tick green but supervised=0" gap) |
| `workflow-file` | CRITICAL | `.github/workflows/bot-cron.yml` is absent on the governance default branch |
| `bot-cron-enabled` | CRITICAL | the workflow is `disabled_*` — GitHub auto-disables a schedule after ~60 days; re-enable with `gh workflow enable bot-cron.yml` |
| `gate-liveness` | WARN (FAIL in `--e2e`) | the last tick is older than 2× the configured cadence — a *silently dead* cron. WARN on a plain run (GitHub cron lag is real); hard-FAIL only in `--e2e`, right after you dispatched a tick |
| `ready-to-merge-label` | CRITICAL | the `ready-to-merge` label is missing on a supervised repo |
| `squash-allowed` | CRITICAL | squash merging is disabled on a supervised repo (the gate merges via squash) |
| `bot-state-branch` | WARN | the `bot-state` branch does not exist yet — expected before the first successful tick (which creates it) |
| `adopter-kit-markers` | CRITICAL / WARN | the installed `AGENTS.md`/`CLAUDE.md` kit still has unfilled `{{ }}` markers (FAIL), or no kit was found (WARN) |
| `decision-labels` | INFO | echoes any `decision:*` labels — these are created by the bot at runtime, so their absence is **never** a failure |

## Honest scope (what a PASS does and does not prove)

A PASS means **"setup artifacts are present and the bot is ticking."** It does
**not** prove the gate is functionally correct:

- Secret *values* are unreadable by design. `secrets-present` confirms the three
  secret **names** exist only when you run `gh secret list` yourself; the live
  tick is what proves the PEM actually works.
- `verify-setup` checks artifact presence + liveness, not that a real PR is
  gated correctly. **The functional proof is the live end-to-end rehearsal in
  AGENT_SETUP Step 9** — run that (and `verify-setup --e2e` alongside it) before
  you flip `MERGE_GATE_MERGE_ENABLED=true`.

## As an Actions job (optional)

You can add a manual `workflow_dispatch` job in your **private** governance repo
that runs `python -m multiagent_protocol verify-setup --json` and uploads the
output as an artifact. Keep it in the private mirror only — the report is a
private surface. This is optional; the gate does not require it.
