# Example: multi-domain (5 supervised + self-hosted runner)

Operator runs 5 supervised repos across different product domains. GitHub Actions Free minutes would run out around repo #4 (6 cron ticks/hour × 5 repos × ~2 min per tick = ~3,600 minutes/month, exceeds the 2,000-minute free tier). Solution: self-hosted runner.

## What's different

- `runner_tier: self-hosted`.
- `supervised_repos` has 5 entries.
- `bot_repo` is a separate repo from `governance_repo` (the bot's code is large enough to merit its own repo).
- `agent_registry.yml` extends `models:` with specific model identifiers for stricter L4 (no `["*"]` wildcards).

See `docs/guide/self-hosted-runner.md` (TODO) for the self-hosted runner setup.
