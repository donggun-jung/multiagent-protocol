# Examples — which one should I start from?

Three progressive starter configurations. Pick the one whose shape matches your fleet today; switch later by editing your governance repo's `config/*.yml` (no migration needed — schemas are additive).

## Decision tree

| Starting state                                              | Pick                                            |
|-------------------------------------------------------------|-------------------------------------------------|
| 1 repo, just me, GitHub Actions Free, basic AI agents       | [`solo-developer/`](solo-developer/)            |
| 2–3 repos, 1–2 reviewers besides me, still on Free tier     | [`small-team/`](small-team/)                    |
| 4+ repos, mixed product domains, ready for self-hosted runner | [`multi-domain/`](multi-domain/)                |

If unsure, **start with `solo-developer/`**. You can graduate to `small-team/` or `multi-domain/` later by copying the additional config blocks; the bot tolerates missing optional config files.

## What differs across the three

| Aspect                            | solo-developer  | small-team       | multi-domain                                       |
|-----------------------------------|------------------|------------------|----------------------------------------------------|
| `supervised_repos` count          | 1                | 3                | 5                                                  |
| `owner.allowlisted_actors`        | 1 (just you)     | 2 (you + reviewer) | 1 (you; team isn't the differentiator here)      |
| `env.runner_tier`                 | `actions-free`   | `actions-free`   | `self-hosted`                                      |
| `bot_repo` distinct from `governance_repo` | no       | no               | yes (bot lives in a dedicated repo)                |
| `decision_inbox.repository` override | no (defaults to governance) | no | optional — see the example for the rationale       |
| `agent_registry.yml` tools        | 7 (5 agents: claude-code, codex, cursor, gemini-cli, aider; + manual + github-actions) | 7 (same as solo) | 5 (3 agents: claude-code, codex, cursor — locked-down; + manual + github-actions) |
| `agent_registry.yml` models       | wildcard `["*"]` per tool | wildcard          | specific model IDs (no wildcards)                  |
| `skills.severity_overrides`       | empty (defaults) | `no_wip_markers: P0` (team policy: WIP blocks) | empty (no team policy yet) |
| Custom skills in `config/skills/` | none             | none             | none (but the structure is ready for per-domain rules) |

## How to copy an example

```bash
# 1. Pick the example you want.
EXAMPLE=solo-developer

# 2. Copy its config/ into your governance repo's root.
cp -r examples/$EXAMPLE/config/ ./config/

# 3. Edit each *.yml — every placeholder (your-github-login, your-github-login/some-repo,
#    your-merge-gate, etc.) needs to be replaced with your actual values.
$EDITOR config/owner.yml config/projects.yml config/env.yml config/skills.yml config/agent_registry.yml

# 4. Validate before commit (catches typos against the schemas).
python3 -m multiagent_protocol check-config

# 5. Commit and follow docs/guide/quick-start.md for App + Actions setup.
```

The wizard at [`docs/wizard/`](../docs/wizard/) does the same thing but interactively; either path produces equivalent config.

## "How do I graduate?"

The transitions are deliberately small.

**solo-developer → small-team:**

1. Append a second repo to `projects.yml` `supervised_repos`.
2. Append a second login to `owner.yml` `allowlisted_actors`.
3. (Optional) Set `skills.severity_overrides.no_wip_markers: P0` if your team agrees.

**small-team → multi-domain:**

1. Append more repos.
2. Move `env.runner_tier` to `self-hosted` (and follow [`docs/guide/self-hosted-runner.md`](../docs/guide/self-hosted-runner.md)).
3. Optionally tighten `agent_registry.yml` `models` from `["*"]` to specific model identifiers (catches "an agent silently bumped to a model I haven't approved yet").
4. Consider setting `projects.yml` `decision_inbox.repository` to a dedicated repo if the inbox volume across many repos is overwhelming the governance repo's Issues tab.

None of these graduations require a re-install of the GitHub App.

## What examples do NOT show

Custom skills (`config/skills/validators/foo.py`, etc.) are covered by [`docs/guide/skills.md`](../docs/guide/skills.md). None of the three examples ships a custom skill by default; we keep the examples minimal so the diff between them is the differentiation, not extra code.

Self-hosted runner deployment is covered by [`docs/guide/self-hosted-runner.md`](../docs/guide/self-hosted-runner.md). The `multi-domain/` example sets `runner_tier: self-hosted` but does not include the docker compose / runner registration steps — those live in the guide.

Mirror cascade (`schemas/mirror_paths.json`) is a separate concern documented in [`docs/concepts/mirror-cascade.md`](../docs/concepts/mirror-cascade.md). All three examples assume a single-repo or few-repo setup where cascade is optional; if you want cross-repo doctrine enforcement, follow the cascade guide independently.
