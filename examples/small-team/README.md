# Example: small team (3 supervised repos + 2 reviewers)

A team of 3 people supervising 3 repos. Two of the three are allowlisted to approve Quadrant D PRs (the third is read-only). Still on `actions-free` tier — 3 repos × ~5 PRs/week is well within GitHub Actions Free minutes.

## What's different from solo-developer

- `allowlisted_actors` lists two people.
- `supervised_repos` has three entries.
- `agent_registry.yml` adds a fourth tool (your team uses Aider extensively).
- `skills.yml` enables a custom skill `require_changelog_on_feat_commits` (assumed copied into `config/skills/branch_hooks/`).
