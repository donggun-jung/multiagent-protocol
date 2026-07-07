# Adopter agent kit

Copy-paste rule files for **your supervised repositories** — they teach any
AI coding agent (Claude Code, Codex, Cursor, …) the discipline the merge
gate enforces: branch+PR only, the five commit trailers, label semantics,
quadrant behavior, break-glass boundaries, and your personal working
preferences.

| File | Goes to | Purpose |
|---|---|---|
| `AGENTS.md` | supervised repo root | canonical agent rules (all vendors read this) |
| `CLAUDE.md` | supervised repo root | Claude Code auto-loads this; it points to `AGENTS.md` |

## Placeholders

`{{...}}` markers are filled during installation —
[`docs/agent-setup/AGENT_SETUP.md`](../../docs/agent-setup/AGENT_SETUP.md)
step 6 does this for you (your setup agent substitutes the values):

| Placeholder | Source |
|---|---|
| `{{REPO_NAME}}` | the supervised repo (`owner/name`) |
| `{{AGENT_TOOLS}}` | `config/agent_registry.yml` → registered tool names |
| `{{MACHINE_HANDLE}}` | `config/agent_registry.yml` → the machine handle for that workstation |
| `{{TICK_MINUTES}}` | your cron cadence (from the schedule you deployed) |
| `{{PREFERENCES_BLOCK}}` | rendered from `config/preferences.yml` (language, report style, autonomy profile, taste ledger, vocabulary) |

Re-run AGENT_SETUP step 6 whenever `config/preferences.yml` changes — the
materialized block in each supervised repo is a copy, and stale copies are
how agents end up following last month's preferences.

## Why materialize instead of linking?

Your agents work inside the supervised repo; the preferences live in your
private governance repo. A link would make every agent session depend on
having the governance repo checked out. A materialized block keeps each
repo self-contained — at the cost of the re-run rule above.
