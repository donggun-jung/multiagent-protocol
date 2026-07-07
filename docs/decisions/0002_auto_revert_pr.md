---
schema_version: 1
adr_number: 2
title: "L2 automatic revert-PR creation (opt-in, default off)"
status: accepted
date: 2026-07-07
authors: ["<owner-github-login>"]
supersedes: null
related: ["docs/concepts/architecture.md", "docs/concepts/four-quadrants.md"]
---

## Context

L2 (post-merge re-validation, `branch_supervisor.revalidate_main`) re-runs the
required checks against each new commit on `main`. On a **real** failure (not an
infra/`cancelled` blip) it opens a `decision:post-merge-revalidation` incident
issue carrying the exact `git revert <sha>` command. Through v1.1 it stops
there: it **detects** the bad merge and files the incident, but it does not
**author** the revert PR.

Authoring a commit into a supervised repo is itself a **Quadrant-D** action
(irreversible-ish + critical: the bot writing to someone else's `main`-adjacent
branch), which is why v1.1 deliberately left it to the operator — the same
gating rationale that keeps mirror auto-cascade manual
(`docs/concepts/architecture.md` § "Module 4"). The engine is otherwise
**API-only** (`requests`) with a GitHub App installation token, and GitHub has
**no revert REST endpoint** — so even when we choose to author the revert, we
cannot do it with a single API call.

The operator asked for the bot to close this last manual step: when a bad merge
is detected, open the revert PR automatically so recovery is one review away,
not one `git` session away.

## Decision

Add an **opt-in, default-off** knob `env.yml` `auto_revert_pr: false`. When
`true`, the L2 real-failure path ALSO:

1. Reuses the incident issue it already opens (opening it FIRST so its number is
   available), then
2. Creates branch `revert/<bad-sha7>` in the supervised repo and a PR that
   reverts the bad commit, then
3. Links that PR in the incident issue body.

Because there is no revert API, the revert itself is done with git over a
**shallow clone** (`git clone --depth 50 --branch main
https://x-access-token:<token>@github.com/<owner>/<repo>`), `git revert
--no-edit <sha>`, and a push of the new branch; the PR is then opened via the
existing API client. The revert commit message is **amended** to carry the five
`Agent-*` trailers + `Task-Ref` (`Agent-Tool: github-actions`, `Agent-Model:
n/a`, `Agent-Session: s_bot-revert`, `Agent-Machine: bot`, `Task-Ref:
Issue#<incident>`) so the merge gate's own C5/L4 can evaluate the revert PR like
any other.

Four properties are load-bearing:

- **The revert PR goes through the normal gate.** It is **not** auto-labelled
  `ready-to-merge`. That is the entire point — a bot-authored change into a
  supervised repo must still be owner/classifier-gated (label it
  `decision:auto-revert` to fast-track it to Quadrant C; the classifier only
  honours that label from an owner/bot applier bound to the head — see
  `classifier_auto_revert`).
- **Graceful degradation.** EVERY failure (clone, revert conflict, push, PR
  open, missing token) degrades to exactly today's behaviour: the incident is
  still opened, and the failure reason is appended to its body. The tick is
  never crashed and the bot commits nothing on failure. The installation token
  is redacted from any git output written into the (potentially public)
  incident body.
- **Idempotency.** If the `revert/<sha7>` branch — or an open PR from it —
  already exists, the existing one is linked; no duplicate branch or PR is
  created. (A tick that dies between push and PR-open re-runs and opens the PR
  against the already-pushed branch without re-cloning.)
- **Testability.** All git work goes through an injected `runner` callable
  (default `subprocess`), so the unit tests exercise clone → revert → amend →
  push → PR with the subprocess fully mocked and no real git; the GitHub side
  uses the existing fakes.

Default-off is the **Quadrant-D** rationale in the config: shipping this on by
default would make every fresh install's bot start writing to supervised repos
without the operator having opted into that authority. An operator turns it on
consciously (`auto_revert_pr: true`) once they want automated recovery.

## Consequences

- A real post-merge regression on `main`, in a repo with `auto_revert_pr: true`,
  now produces a ready-to-review revert PR automatically, cutting mean-time-to-
  recovery. The operator still reviews/merges it (or fast-tracks with the
  `decision:auto-revert` label) — the gate is not bypassed.
- The bot's App installation must have `contents: write` (to push the revert
  branch) and `pull_requests: write` (to open the PR) on the supervised repo.
  These are additive to the permissions the bot already needs; a missing
  permission degrades to incident-only (the push/PR fails gracefully), it does
  not crash the tick.
- The 5-minute tick now performs a shallow clone + a couple of git commands on a
  real L2 failure when the feature is on. This is bounded (depth 50, a 180s
  subprocess timeout) and only fires on the rare real-failure path, so the
  rate-limit / wall-clock budget is unaffected in steady state.
- New `github_api` surface: `create_pull_request`, `list_prs_for_head`,
  `update_issue_body`. New module `src/multiagent_protocol/auto_revert.py`.
- The matrix row "L2 auto-revert PR **creation**" moves from ❌ to ✅ **(opt-in)**.

## Alternatives considered

- **Keep it manual (status quo, v1.1).** Rejected as the default is retained,
  but the operator explicitly asked to remove the last manual step for repos
  where they want it — hence opt-in rather than never.
- **Author the revert purely via the API (no git).** Not possible: GitHub has no
  revert endpoint, and reconstructing a revert commit tree via the Git Data API
  (blobs/trees/commits) is far more code and far more failure surface than a
  shallow clone + `git revert`, for no benefit.
- **Auto-label the revert `ready-to-merge` so it self-merges.** Rejected: that
  would defeat the whole gate (a bot merging its own commit into `main` with no
  human/classifier check is precisely the self-gating paradox the protocol
  exists to prevent). The revert goes through the gate like everything else.
- **On by default.** Rejected: authoring commits into a supervised repo is a
  Quadrant-D authority the operator must grant deliberately; default-off keeps a
  fresh install's blast radius to detection + incident.
