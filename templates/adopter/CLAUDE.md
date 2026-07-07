# Claude Code Instructions — {{REPO_NAME}}

Read [`AGENTS.md`](AGENTS.md) in this repository root **before any edit** —
it is the single source of agent rules here (merge gate contract, commit
trailers, quadrant behavior, operator preferences). This file exists only so
Claude Code loads the pointer automatically.

Non-negotiable minimum, in case you read nothing else:

- Never commit to `main`; branch + PR.
- Every commit needs the five `Agent-*` trailers exactly as specified in
  `AGENTS.md` §2.
- `ready-to-merge` label only when done AND green.
- `decision:pending-owner` issue on your PR → stop and wait for the operator.
- Gate won't clear and you don't know why → report **BLOCKED**, don't bypass.
