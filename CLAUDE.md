# Claude Code Instructions

See [`AGENTS.md`](AGENTS.md) for the full read order. This file is here so Claude Code (and any agent that looks for `CLAUDE.md` first) finds the same instructions as agents that look for `AGENTS.md` first. They are the same file; do not duplicate the content here.

## Quick rules

- Read order: `AGENTS.md` → pick lane → follow.
- Commit trailers: required (see `AGENTS.md` non-negotiable #5).
- No personal data: see `AGENTS.md` non-negotiable #1.
- Bot does not gate its own PRs: see `AGENTS.md` non-negotiable #7.

## Completion contract

If you change files, completion requires: commit, push, CI green, and (if doctrine changed) a `docs/concepts/*.md` update in the same PR. Report `BLOCKED` rather than silently bypass any gate.
