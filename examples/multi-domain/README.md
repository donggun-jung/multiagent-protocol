# Example: multi-domain (5 supervised repos + self-hosted runner + locked-down agent registry)

An operator running 5 supervised repos at a scale where GitHub Actions Free minutes run out mid-month and the `agent_registry.yml` `["*"]` wildcards in earlier examples are no longer the right tradeoff. Switches to `runner_tier: self-hosted` and lists model identifiers explicitly.

> **About the example's "multi-domain" name.** The repo names here (`finance-app`, `marketing-site`, …) are conventional product names, not formally separated domains with per-domain rules. The differentiator from the `small-team/` example is **count + runner tier + agent-registry strictness**, not per-domain classifier rules. Per-domain rule files at the config level are a planned v0.2.0 feature; today, per-domain rules are written as custom Validator skills under `config/skills/validators/` (see [`docs/guide/skills.md`](../../docs/guide/skills.md)).

## What's different from `small-team`

| | `small-team` | `multi-domain` |
|---|---|---|
| `supervised_repos` count | 3 | 5 |
| `env.runner_tier` | `actions-free` | `self-hosted` |
| `bot_repo` distinct from `governance_repo` | no | yes (`example-org/merge-gate-bot`) |
| `agent_registry.yml` tools count | 5 (claude-code, codex, cursor, gemini-cli, aider) | 3 (claude-code, codex, cursor only) |
| `agent_registry.yml` `models` per tool | wildcard `["*"]` | explicit model identifiers (no wildcards) |
| `agent_registry.yml` `machines` | empty (any machine handle accepted) | 4 known machine handles listed |
| `skills.severity_overrides` | `no_wip_markers: P0` (team policy) | empty (no policy chosen — multi-domain owners should pick per-domain) |

Three callouts:

1. **Locked-down agent registry is the main security/flexibility tradeoff at this scale.** `solo-developer/` uses `["*"]` wildcards so any new model identifier from a registered tool is accepted. With 5 repos and multiple humans using AI agents, a teammate switching their Claude Code config to a beta model would silently start writing commits the gate accepts but you have not vetted. `multi-domain/` requires you to add new model identifiers explicitly, surfacing the "you bumped a model" signal as a PR you can refuse to land.
2. **Separate bot repo.** With a self-hosted runner and 5 supervised repos, the bot's own self-update PRs become substantial. Putting them in a dedicated `bot_repo` keeps them out of the governance repo's PR list (which already carries the Decision Inbox issues for all 5 supervised repos). Both repos still get the same App installation; the bot just merges its own changes in the dedicated repo.
3. **No `severity_overrides` policy yet.** The `multi-domain` example deliberately leaves this empty rather than inherit the `small-team` choice. With multiple domains, a single severity policy across all 5 repos is rarely right — pick per-skill policies for your fleet.

## How to adapt

1. Replace every `example-org` with your actual GitHub org.
2. Replace `example-org-owner` with your actual GitHub login.
3. Replace the 5 repo names with your actual 5 repos (remove rows if you only have 4).
4. In `agent_registry.yml`, update the `models` lists to the exact model identifiers your team uses today. Add new entries when you ship; the L4 identity gate will reject unknown identifiers.
5. Update `machines` with your actual machine handles (laptops, desktops, VPS runners).
6. Follow [`docs/guide/self-hosted-runner.md`](../../docs/guide/self-hosted-runner.md) to register the self-hosted runner. The bot-cron workflow needs `runs-on: [self-hosted, multiagent-protocol]` to pick it up.

## What this example does NOT include

- Per-domain classifier rules (no `config/skills/classifier/per_domain_*.py` files). The example sets the **stage** for per-domain rules — distinct tools, distinct machines, distinct bot repo — but does not ship the rules themselves. Writing them is your team's call; [`docs/guide/skills.md`](../../docs/guide/skills.md) "Common skill patterns" is the starting point.
- Custom skills directory (`config/skills/`). The directory is reserved by the loader; the example does not commit any custom skill code.

See [`../README.md`](../README.md) for the cross-example decision tree.
