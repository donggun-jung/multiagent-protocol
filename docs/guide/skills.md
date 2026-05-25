# Writing your own skill

This guide walks through writing a custom skill — a Validator, ClassifierRule, or BranchHook — and adding it to your installation. Read [`docs/concepts/skills-plugin.md`](../concepts/skills-plugin.md) first for the interfaces and security model.

We will write a validator that refuses to merge any PR whose commit subjects contain `WIP:` (work-in-progress markers).

## Step 1 — Pick the right interface

Three choices:

- **Validator** — runs on each open PR, returns pass/fail. The verdict feeds into L1.
- **ClassifierRule** — runs on each open PR, returns a Quadrant A/B/C/D vote. The verdict feeds into the classifier.
- **BranchHook** — runs on each commit on `main`, returns an incident or nothing. The verdict feeds into L5-style audits.

"Refuse to merge PRs with WIP markers" is a per-PR pass/fail decision → **Validator**.

## Step 2 — Write the file

Create `config/skills/validators/no_wip_markers.py` in your fork of the protocol:

```python
"""Refuse to merge a PR whose commit subjects contain WIP markers."""

from multiagent_protocol.skills.base import Validator, ValidationResult


class NoWipMarkers:
    name = "no_wip_markers"
    severity = "P0"  # block immediately; not "P3" advisory

    # Subjects starting with these prefixes are WIP and block the PR.
    WIP_PREFIXES = ("WIP:", "wip:", "DRAFT:", "TODO:", "FIXME:")

    def check(self, pr_context):
        for c in pr_context.commits:
            subject = c.subject.strip()
            for prefix in self.WIP_PREFIXES:
                if subject.startswith(prefix):
                    return ValidationResult(
                        passed=False,
                        failure_reason=(
                            f"Commit {c.sha[:7]} subject starts with '{prefix}' — "
                            f"WIP/DRAFT commits must be amended before merging."
                        ),
                    )
        return ValidationResult(passed=True, failure_reason=None)
```

Note:

- The class name (`NoWipMarkers`) does not have to match the file name, but it should.
- `name` is the identifier used in audit logs and the skill-load metric.
- `severity` is `P0` (blocking), `P1` (60-day burn-in warn → block), `P2` (warn only), or `P3` (audit only).
- The skill **cannot** import network libraries (`requests`, `urllib`, `socket`, etc.). The loader rejects skills with such imports.

## Step 3 — Register the skill

In your `config/skills.yml`:

```yaml
enabled:
  - no_wip_markers

disabled: []

severity_overrides: {}
```

By default any file in `config/skills/{validators,classifier,branch_hooks}/` is auto-loaded. Listing under `enabled:` is optional but explicit (and means the bot warns if the file is missing).

## Step 4 — Commit and push

```bash
git checkout -b add-no-wip-marker-skill
git add config/skills/validators/no_wip_markers.py config/skills.yml
git commit -m "skill: refuse PRs with WIP-prefixed commit subjects

Agent-Tool: claude-code  # or whatever you used
Agent-Model: ...
Agent-Session: s_skill-wip-marker
Agent-Machine: <your-machine-handle>
Task-Ref: round-N/no-wip-skill
"
git push -u origin add-no-wip-marker-skill
gh pr create --fill
```

The PR itself goes through the gate. The classifier sees `config/skills/` as a Tier-4 (machine contract) change → typically Quadrant B (auto-merge with audit issue). Once merged, the bot loads the skill on the next tick.

## Step 5 — Verify the skill loaded

Look at the next cron tick's workflow log:

```
[multiagent-protocol] skills loaded: 6 builtin + 1 user = 7 total
[multiagent-protocol]   user skills: no_wip_markers (validators)
```

If you see `skill_load_failed: no_wip_markers — <reason>`, fix the reason and push again.

## Step 6 — Test the skill

In a supervised repo, push a commit with subject `WIP: testing the new skill`. Open a PR. Within 5 minutes the bot comments:

```
Merge Gate L1 — merge blocked:
- no_wip_markers: Commit abcd123 subject starts with 'WIP:' — WIP/DRAFT commits must be amended before merging.
```

Amend the commit to remove the prefix; the bot re-evaluates and proceeds.

## Common skill patterns

### Validator: "PR description mentions an ADR for irreversible changes"

```python
class IrreversibleChangesNeedAdr:
    name = "irreversible_changes_need_adr"
    severity = "P0"

    def check(self, pr_context):
        # Heuristic: if any file under schemas/ or src/multiagent_protocol/skills/builtin/
        # changes, require the PR description to mention "ADR-" or "docs/decisions/".
        critical = any(
            f.path.startswith("schemas/") or
            f.path.startswith("src/multiagent_protocol/skills/builtin/")
            for f in pr_context.files_changed
        )
        if not critical:
            return ValidationResult(passed=True, failure_reason=None)
        body = (pr_context.body or "").lower()
        if "adr-" in body or "docs/decisions/" in body:
            return ValidationResult(passed=True, failure_reason=None)
        return ValidationResult(
            passed=False,
            failure_reason=(
                "This PR modifies a critical path. PR description must reference "
                "an ADR (write ADR-NNNN or docs/decisions/NNNN_topic.md)."
            ),
        )
```

### ClassifierRule: "PRs that modify CI workflows are always Quadrant D"

```python
from multiagent_protocol.skills.base import ClassifierRule, ClassifierVote


class CiWorkflowsAreD:
    name = "ci_workflows_are_d"

    def evaluate(self, pr_context):
        touches_ci = any(
            f.path.startswith(".github/workflows/")
            for f in pr_context.files_changed
        )
        if touches_ci:
            return ClassifierVote(
                quadrant="D",
                reasoning="PR modifies a CI workflow; owner approval required.",
            )
        return ClassifierVote(quadrant="A", reasoning="no CI changes")
```

(Note: a `ClassifierVote` of A from a user rule does not lower the verdict; built-in rules and other user rules can still vote higher. Voting A simply means "this rule has no opinion above the baseline.")

### BranchHook: "Refuse to merge a commit whose body references a file that does not exist"

```python
from multiagent_protocol.skills.base import BranchHook, BranchHookResult
import re


class HallucinationGuard:
    name = "hallucination_guard"

    FILE_REF_RE = re.compile(r"`([a-zA-Z0-9_./-]+\.[a-zA-Z]{1,5})`")

    def on_commit(self, commit_context):
        body = commit_context.body or ""
        for m in self.FILE_REF_RE.finditer(body):
            path = m.group(1)
            if not commit_context.repo_has_file_at_sha(path, commit_context.sha):
                return BranchHookResult(
                    incident_label="decision:hallucination-detected",
                    incident_body=(
                        f"Commit {commit_context.sha[:7]} references `{path}` "
                        f"in its body, but no file at that path exists at the "
                        f"merged SHA. Likely an AI-generated hallucinated reference."
                    ),
                )
        return BranchHookResult(incident_label=None, incident_body=None)
```

(This is essentially the built-in `hook_hallucination_guard.py`; reproduced here for illustration.)

## Testing your skill

Skills are pure functions of context — easy to unit test. Add `tests/skills/test_no_wip_markers.py`:

```python
# The fixtures live in tests/conftest.py at the repo root; pytest auto-imports them.
from tests.conftest import make_pr_context, make_commit
from config.skills.validators.no_wip_markers import NoWipMarkers


def test_wip_subject_blocks():
    skill = NoWipMarkers()
    ctx = make_pr_context(commits=[make_commit(subject="WIP: still working")])
    result = skill.check(ctx)
    assert not result.passed
    assert "WIP:" in result.failure_reason


def test_clean_subject_passes():
    skill = NoWipMarkers()
    ctx = make_pr_context(commits=[make_commit(subject="feat: add login screen")])
    result = skill.check(ctx)
    assert result.passed
```

Run `pytest tests/skills/test_no_wip_markers.py` before opening the PR.

## Disabling a skill (yours or built-in)

In `config/skills.yml`:

```yaml
disabled:
  - hook_hallucination_guard  # I am running on a prototype repo with intentional placeholders
  - no_wip_markers             # I changed my mind
```

The skill stays loaded but its result is ignored on every tick. To re-enable, remove the entry. (Disabling a built-in **L1 validator** — C1, C2, C3, C4, C5 — is not permitted. Use `severity_overrides` instead.)

## When you cannot do it with a skill

Some things require core changes, not skills:

- New PR-context field (e.g., "give me the file size deltas"). Open an Issue tagged `enhancement` proposing the field addition.
- Network-dependent checks (e.g., "ask an external service whether this PR is safe"). Skills cannot make network calls; you would need either (a) a core extension that exposes the network result as a context field, or (b) a separate GitHub Action that runs alongside the bot and posts a check-run that the bot's existing C2 validator reads.

Most useful skills do not need either — they are pure functions of the PR's content.
