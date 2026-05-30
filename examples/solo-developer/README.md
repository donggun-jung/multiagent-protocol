# Example: solo developer (1 supervised repo)

The simplest setup: one person, one repo they want supervised. The bot runs on GitHub Actions Free tier, no self-hosted runner.

## What this example shows

- Single `supervised_repos:` entry (just the one repo).
- `runner_tier: actions-free` — no VPS needed.
- Default skills + default classifier rules.
- `agent_registry.yml` with the most common AI tools.

## How to use

1. Copy `config/*.yml` into your fork of `multiagent-protocol`.
2. Replace `your-github-login` with your actual GitHub login.
3. Replace `your-github-login/some-repo` with your supervised repo's path.
4. Push to `main`, install the GitHub App, set Actions secrets, enable the cron workflow.

See `docs/guide/quick-start.md` for the full walkthrough, and [`../README.md`](../README.md) for the decision tree comparing all three examples.
