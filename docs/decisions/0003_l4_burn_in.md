---
schema_version: 1
adr_number: 3
title: "L4 60-day burn-in auto-promotion (opt-in, default off)"
status: accepted
date: 2026-07-07
authors: ["<owner-github-login>"]
supersedes: null
related: ["docs/concepts/four-quadrants.md", "docs/concepts/architecture.md"]
---

## Context

The L4 identity gate (`validator_agent_registry`) checks that each commit's
`(Agent-Tool, Agent-Model)` pair is declared in the operator's
`config/agent_registry.yml`. Per the burn-in doctrine
(`docs/concepts/four-quadrants.md` § "L4 burn-in: 60-day advisory window"), a
newly-added agent vendor/model should be **advisory** for a window before it is
promoted to a hard block — otherwise adding `Aider 0.x` or `Codex 2.0` to a
fleet that has been running `Claude Code` for months would break every PR the
day the new tool first commits.

Through v1.1 the gate ships advisory-only (severity `P2` — warns, does not
block). Promotion to a hard block is **manual**: the operator sets
`config/skills.yml` `severity_overrides: {validator_agent_registry: P0}`. The
**automatic** advisory→block clock described in the doctrine was unbuilt.

The operator asked for that automatic clock, without giving up manual control.

## Decision

Add an **opt-in, default-off** knob `env.yml` `l4_burn_in_days: 0` (`0` = off;
a positive integer = the burn-in window in days; the doctrine window is `60`).

Each tick, when `l4_burn_in_days > 0`:

- The clock's start (`advisory_started_at`) is written to a small JSON file on
  the **bot-state branch** — `bot-state/l4_burn_in.json` — the FIRST tick the
  feature is on and the registry validator is at advisory severity. This reuses
  the exact durable-state mechanism `branch_supervisor` uses for watermarks (a
  dedicated non-`main` branch, so the bot's own `main` scanners never observe
  the state commit), and rides on the branch `BotStateStore.load()` already
  ensures exists.
- Once `now >= advisory_started_at + l4_burn_in_days`, the effective severity of
  `validator_agent_registry` is promoted to `P0` (hard block), mutating the
  loaded validator instance in place before any PR is gated that tick.
- **The operator always wins.** If `skills.severity_overrides` pins
  `validator_agent_registry` in EITHER direction (`P0` or back to `P2`), the
  burn-in clock is **inert**: explicit config is authoritative and the state
  file is not even managed. This makes "manual promote" (v1.1) and "manual keep-
  advisory" both continue to work unchanged, and lets an operator freeze the
  gate at advisory indefinitely even with the clock armed.

Two design choices are load-bearing:

- **Fail-SAFE (the deliberate opposite of the watermark store).** A missing or
  corrupt `l4_burn_in.json` is **rewritten** with a fresh `now` start and the
  gate stays advisory that tick. A watermark fails *closed* (an unreadable
  watermark must never silently skip a scan); a burn-in clock fails *safe* (an
  unreadable clock must never be the reason a PR is hard-blocked). Blocking a
  merge on the basis of corrupt state would be a worse failure than briefly
  restarting the advisory window.
- **Injected clock + "log once."** The promotion logic never calls
  `datetime.now()` inline — `main()` computes one tick clock (`now`, injectable
  for tests) and threads it in. The info line announcing that promotion is
  active is emitted only on the tick the threshold is first crossed; a durable
  `promoted_at` field in the same file keeps later ticks quiet, since the bot is
  stateless across ticks and cannot otherwise know "the first time".

Default-off keeps a fresh install's L4 gate exactly where v1.1 left it
(advisory), so no upgrade silently flips an operator's registry gate to hard-
block. An operator arms it consciously (`l4_burn_in_days: 60`).

## Consequences

- With `l4_burn_in_days: 60` set, an operator who adds a new tool/model to
  `agent_registry.yml` gets 60 advisory days, after which an unregistered
  tool/model **fails** L4 (a hard block, distinct from C5's trailer-format
  check) — automatically, with no manual `severity_overrides` edit.
- The bot-state branch gains one small file, `bot-state/l4_burn_in.json`,
  written at most twice per clock lifetime (start + promotion). Steady-state
  ticks add zero writes to it (unchanged start, already-recorded promotion).
- Because the clock lives on the governance repo's bot-state branch, promotion
  applies uniformly across every installation's runtime the tick processes.
- New module `src/multiagent_protocol/l4_burn_in.py`; `main()` gains an
  injectable `now` parameter and an `l4_promoted` tick metric.
- The matrix row "L4 60-day burn-in auto-promotion" moves from ❌ to ✅
  **(opt-in)**; the manual `severity_overrides` promotion remains fully
  supported and, when set, overrides the clock.

## Alternatives considered

- **Keep it manual (status quo, v1.1).** Retained as the default and as an
  always-wins override, but the operator asked for the automatic clock the
  doctrine already promises — hence opt-in rather than never.
- **On by default with a 60-day window.** Rejected: an upgrade must not silently
  arm a clock that will later hard-block an operator's PRs. Default-off; the
  operator opts in.
- **Store the clock in the watermarks file.** Rejected: the spec (and clean
  separation) calls for a dedicated `l4_burn_in.json`, and the two files have
  opposite failure policies (watermarks fail closed, the clock fails safe) — co-
  mingling them would blur that boundary.
- **Fail-closed on a corrupt clock (mirror the watermark store).** Rejected: a
  corrupt clock hard-blocking merges is a worse outcome than restarting the
  advisory window. The asymmetry is intentional and documented above.
- **Reset the burn-in window when the agent produces a Quadrant-D rejection**
  (the doctrine's "clock resets" clause). Deferred: the v1.2 clock is a simple
  elapsed-time promotion; wiring rejection-driven resets needs per-agent
  rejection tracking and is a follow-up, not a v1.2 blocker.
