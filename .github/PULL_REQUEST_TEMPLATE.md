<!--
Thanks for the contribution. Please fill in this template so the bot can
classify your PR correctly.

If your PR is small (typo, single-file doc fix) you can shorten this — the
classifier will still pick a reasonable quadrant.
-->

## Summary

<!-- 1-3 sentences. What does this change and why? -->

## Reversibility

- [ ] **Reversible** — can be undone by `git revert` with no data/config side effects.
- [ ] **Irreversible** — touches stored state, deletes data, changes external systems, or otherwise cannot be cleanly reverted.

## Criticality

- [ ] **Critical path** — touches the bot's authentication, merge logic, classifier, or any file under `src/multiagent_protocol/`.
- [ ] **Non-critical** — docs, examples, tests, comments only.

## Quadrant (filled by classifier; you may pre-fill if confident)

- [ ] **A** — Reversible + Non-critical (auto-approve)
- [ ] **B** — Reversible + Critical (auto-approve with audit issue)
- [ ] **C** — Irreversible + Non-critical (auto-approve)
- [ ] **D** — Irreversible + Critical (owner approval required)

## Testing

<!-- How did you verify this change? Tests added? -->

## Agent-* trailers (required if any commit was authored or co-authored by an AI agent)

Each commit message in this PR should include trailers in this format:

```
Agent-Tool: <claude-code|codex|cursor|gemini-cli|aider|manual|github-actions>
Agent-Model: <model identifier or "n/a">
Agent-Session: s_<2-14 lowercase alphanumeric/hyphen><alphanumeric>   # full regex: ^s_[a-z0-9-]{2,14}[a-z0-9]$
Agent-Machine: <your-machine-handle>
Task-Ref: <Issue#N|PR#N|none|round-N/topic>
```

The bot validates these on every commit; a PR with malformed trailers will fail the gate.

## Checklist

- [ ] I have read [`CONTRIBUTING.md`](../CONTRIBUTING.md).
- [ ] No hardcoded personal data (usernames, emails, machine names, VPS hostnames) in the diff.
- [ ] Tests pass locally (`pytest`).
- [ ] Documentation updated if behavior changed.
- [ ] CHANGELOG entry added under `[Unreleased]` for user-visible changes.
