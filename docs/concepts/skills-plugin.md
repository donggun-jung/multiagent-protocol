# Skills plugin interface

`multiagent-protocol` is intentionally small in its core (4 modules, ~3 kLOC). Anything the core does not need to know is loaded as a **skill** — a Python module that implements one of three plugin interfaces. Skills live in `src/multiagent_protocol/skills/builtin/` (shipped with the project) or `config/skills/` (user-added).

This document specifies the three interfaces, the loader, and the security model.

## The three interfaces

```python
# src/multiagent_protocol/skills/base.py

from typing import Protocol, Literal

Quadrant = Literal["A", "B", "C", "D"]
Severity = Literal["P0", "P1", "P2", "P3"]


class ValidationResult:
    passed: bool
    failure_reason: str | None  # e.g., "C5: missing trailer — Agent-Session"


class Validator(Protocol):
    """A check that contributes to L1 / L2 verdicts.

    Built-in examples: trailer validator, classifier-publisher-identity validator,
    ready-to-merge label validator.
    """
    name: str
    severity: Severity

    def check(self, pr_context: "PRContext") -> ValidationResult: ...


class ClassifierVote:
    quadrant: Quadrant
    reasoning: str   # short, human-readable; appended to the audit log


class ClassifierRule(Protocol):
    """A rule that contributes one vote to the 4-quadrant classifier.

    All registered rules vote; the final classifier verdict is the
    MAXIMUM quadrant across all votes (D > B > C > A).

    Built-in examples: path-classifier, empty-PR, bot-self-repo,
    agent-session-malformed.
    """
    name: str

    def evaluate(self, pr_context: "PRContext") -> ClassifierVote: ...


class BranchHookResult:
    incident_label: str | None  # e.g., "decision:break-glass-unaudited"
    incident_body: str | None


class BranchHook(Protocol):
    """A hook that runs once per commit on main, after L2 + L5.

    Built-in examples: L5 break-glass auditor.
    User-added examples: changelog-required-on-feat-commits,
                          monthly-report-after-30-merges.
    """
    name: str

    def on_commit(self, commit: "CommitContext") -> BranchHookResult: ...
```

The `PRContext` and `CommitContext` types are read-only data classes. They expose the fields a skill is likely to need:

- `pr_context.number`, `head_sha`, `base_sha`, `labels`, `commits`, `files_changed`, `author_login`, …
- `commit_context.sha`, `subject`, `body`, `trailers`, `author_login`, `parents`, …

A skill cannot mutate them or make GitHub API calls directly. If a skill needs information the context does not expose, the protocol grows the context — not the skill's privileges. See "Security model" below.

## Loader

The loader runs at bot startup. It scans:

1. `src/multiagent_protocol/skills/builtin/*.py` — always loaded.
2. `config/skills/{validators,classifier,branch_hooks}/*.py` — loaded if the directory exists.

Each `.py` file must export exactly one class implementing one of the three Protocols above. The class name is the skill identifier (used in audit logs, error messages, the skills-status dashboard).

A skill that fails to import (syntax error, missing dependency) is logged as `skill_load_failed` in the tick metrics and the bot continues with the remaining skills loaded. Built-in skills failing to load is a hard error (bot exits 1); user-added skills failing to load is a soft warning.

User-added skills run **after** built-ins. They cannot:

- Lower a quadrant verdict (a `ClassifierVote` of A from a user skill is ignored if any built-in or other-user skill voted higher).
- Skip a built-in validator (the L1 5 conditions C1-C5 always run, regardless of what user skills say).
- Modify or delete records in the audit log.

## Security model

Skills run in the same Python process as the bot. They have full access to the process memory, the GitHub API token, and the file system. **There is no sandbox.** The protocol's threat model assumes:

- The operator who installs the bot also writes (or vets) every skill in `config/skills/`.
- A malicious skill is equivalent to a malicious operator — equally dangerous, equally out-of-scope for the bot to prevent.

Mitigations the protocol does apply:

- Skills cannot make raw HTTP requests. The `github_api` client is injected into context only for built-in skills; user-added skills receive a read-only view (`PRContext` / `CommitContext`) plus a `bot_logger` for emitting events.
- Skills cannot import network libraries. The loader checks `import` statements at load time; a user-added skill that imports `requests`, `urllib`, `socket`, etc. fails with `skill_imports_network` (this is a heuristic, not bulletproof — sophisticated bypass is not in scope).
- Skills run with a 1-second wall-clock budget per invocation. A skill that runs longer is terminated and its verdict is treated as "pass" (no contribution).

If you want to add a skill that needs network access (e.g., querying an external API), the right answer is to: (a) write the network code in the core as an extension to `PRContext`, with explicit ADR justifying the new external dependency; (b) then let your skill consume the context field.

## Built-in skills

Shipped with the project:

### Validators (`src/multiagent_protocol/skills/builtin/`)

| File                              | What it checks                                             |
|-----------------------------------|------------------------------------------------------------|
| `validator_ready_to_merge.py`     | C1 — label present + applied by allowlisted actor          |
| `validator_ci_green.py`           | C2 — all required checks completed with `success`          |
| `validator_owner_approval.py`     | C3 — owner reaction or classifier auto-approval            |
| `validator_base_up_to_date.py`    | C4 — PR base SHA equals `main` HEAD                        |
| `validator_trailers.py`           | C5 — every commit has all 5 required `Agent-*` trailers    |
| `validator_classifier_publisher.py` | classifier-judgment check-run must be by canonical App   |

### Classifier rules

| File                              | What it votes for                                          |
|-----------------------------------|------------------------------------------------------------|
| `classifier_path_default.py`      | Quadrant from `pr_context.files_changed` per `classifier_rules.yml` |
| `classifier_empty_pr.py`          | D for any PR with 0 file changes                           |
| `classifier_bot_self_repo.py`     | D for any PR targeting the bot's own repo                  |
| `classifier_auto_revert.py`       | C for PRs labeled `decision:auto-revert`                   |

### Branch hooks

| File                              | What it monitors                                           |
|-----------------------------------|------------------------------------------------------------|
| `hook_break_glass_audit.py`       | L5 — `[break-glass-*]` commits on main + ADR-within-24h    |
| `hook_hallucination_guard.py`     | (general preference) refuses to merge a commit whose body references a file/symbol that does not exist in the repo at the merged SHA |

The `hallucination_guard` hook is on by default because hallucinated references are one of the highest-frequency failure modes for AI-generated commits. Disable in `config.skills.disabled` if you have a specific reason.

## Writing your own skill

A minimal validator skill:

```python
# config/skills/validators/no_todos_in_prod.py

from multiagent_protocol.skills.base import Validator, ValidationResult


class NoTodosInProd:
    """Refuse to merge a PR whose commit subjects contain 'TODO'."""

    name = "no_todos_in_prod"
    severity = "P1"  # warn but don't block until 60-day burn-in

    def check(self, pr_context):
        for c in pr_context.commits:
            if "TODO" in c.subject.upper():
                return ValidationResult(
                    passed=False,
                    failure_reason=f"Commit {c.sha[:7]} subject contains TODO",
                )
        return ValidationResult(passed=True, failure_reason=None)
```

Drop the file into `config/skills/validators/`, push, and the bot picks it up on the next tick. The bot's tick metrics will include `skills_loaded` with your skill name; if you do not see it, check the bot's workflow log for `skill_load_failed`.

## Disabling a built-in skill

In `config/skills.yml`:

```yaml
disabled:
  - hook_hallucination_guard  # I am running on a fast-moving prototype repo
```

The skill is loaded but its result is ignored. Disabling a built-in **validator** (C1-C5) is not permitted — the loader refuses with `cannot_disable_required_validator`. Use `config.skills.severity_overrides` (note: plural — see `schemas/skills.schema.json`) to lower the severity instead if you need a soft warning rather than a block.

## Future: WASM sandbox

A future version of the protocol may run user-added skills in a WASM sandbox (Wasmtime) so the "no network" rule is enforced by the runtime rather than by a heuristic. This is on the R-N+1 candidate list, not currently scheduled.
