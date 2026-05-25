# Example: small team (3 supervised repos + 2 allowlisted reviewers)

A team of two human reviewers supervising three repos. Both reviewers are allowlisted in `owner.yml` — either can approve Quadrant D PRs and either can push break-glass commits. Stays on `actions-free` tier (3 repos × 12 ticks/hour × ~30 seconds/tick ≈ 4.5 hours/month, well under the 2,000-minute free quota).

## What's different from `solo-developer`

| | `solo-developer` | `small-team` |
|---|---|---|
| `owner.yml` `allowlisted_actors` | 1 actor (just you) | 2 actors (`team-lead`, `senior-reviewer`) |
| `projects.yml` `supervised_repos` | 1 | 3 |
| `skills.yml` `severity_overrides` | empty | `no_wip_markers: P0` (team policy: WIP commits block the merge) |

The `agent_registry.yml` is intentionally identical to `solo-developer` — both examples assume the same five AI tool registrations (claude-code / codex / cursor / gemini-cli / aider + the mandatory `manual` and `github-actions`). If your team uses a tool not on that list, add it here.

Three callouts on the differences:

1. **Two allowlisted reviewers means either can resolve Decision Inbox issues.** A 👍 from `team-lead` or from `senior-reviewer` counts. The bot picks the most-recent signal, so if one reviewer 👍's and the other 👎's, the later vote wins.
2. **`no_wip_markers: P0` is a team policy, not a bot-default.** The `solo-developer` example leaves this empty. Bumping to `P0` means a PR with `WIP:` or `DRAFT:` in any commit subject **blocks the merge gate** rather than warning. Discuss with your team before enabling.
3. **`bot_repo` left implicit.** Both `governance_repo` and `bot_repo` point at `example-org/multiagent-protocol`. If your team wants a separate bot-code repo, switch to the `multi-domain/` pattern.

## How to adapt

1. Replace every `example-org` with your actual GitHub org.
2. Replace `team-lead` / `senior-reviewer` with the two actual GitHub logins.
3. Replace `frontend` / `backend` / `shared-libs` with your three supervised repos.
4. If you don't want the WIP-block policy, delete the `severity_overrides:` block (the skill defaults to P1 = warn).

See [`../README.md`](../README.md) for the decision tree and graduation notes.
