"""Tests for built-in classifier rules."""

from __future__ import annotations

from multiagent_protocol.skills.builtin.classifier_bot_self_repo import (
    BotSelfRepoClassifier,
)
from multiagent_protocol.skills.builtin.classifier_empty_pr import EmptyPrClassifier
from multiagent_protocol.skills.builtin.classifier_path_default import (
    PathDefaultClassifier,
)
from multiagent_protocol.types import FileChange

# -- Path default --

def test_path_default_no_files_returns_a(pr_factory):
    pr = pr_factory(files_changed=())
    v = PathDefaultClassifier().evaluate(pr)
    assert v.quadrant == "A"


def test_path_default_doc_change_is_a(pr_factory):
    pr = pr_factory(files_changed=(
        FileChange(path="README.md", status="modified", additions=1, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "A"


def test_path_default_src_change_is_b(pr_factory):
    pr = pr_factory(files_changed=(
        FileChange(
            path="src/multiagent_protocol/skills/builtin/foo.py",
            status="modified", additions=10, deletions=2,
        ),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "B"


def test_path_default_src_deletion_is_d(pr_factory):
    pr = pr_factory(files_changed=(
        FileChange(path="src/multiagent_protocol/classifier.py",
                   status="removed", additions=0, deletions=120),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "D"


def test_path_default_doc_deletion_is_c(pr_factory):
    pr = pr_factory(files_changed=(
        FileChange(path="docs/old.md", status="removed",
                   additions=0, deletions=12),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "C"


def test_path_default_workflow_change_is_d(pr_factory):
    # A1: a CI workflow definition governs the gate's runtime (and a PR-introduced
    # workflow can forge a green check), so MODIFYING it is always Quadrant D.
    pr = pr_factory(files_changed=(
        FileChange(path=".github/workflows/tests.yml",
                   status="modified", additions=5, deletions=1),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "D"


def test_path_default_concept_doc_change_is_d(pr_factory):
    # A1 (crown jewel): docs/concepts/* is the operating doctrine — "always
    # Quadrant D regardless of the diff". A *modification* must route to the
    # owner, so an agent cannot quietly rewrite the rules that constrain it.
    pr = pr_factory(files_changed=(
        FileChange(path="docs/concepts/architecture.md",
                   status="modified", additions=1, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "D"


def test_path_default_config_change_is_d(pr_factory):
    # A1: a PR editing the gate's OWN config (config/projects.yml — audit_only_repos,
    # required_checks, the agent registry) reconfigures enforcement itself, so a
    # MODIFY is always Quadrant D (owner-gated), never auto-merged.
    pr = pr_factory(files_changed=(
        FileChange(path="config/projects.yml",
                   status="modified", additions=2, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "D"


def test_path_default_governance_and_schema_and_botstate_modify_are_d(pr_factory):
    # A1: governance logic, JSON schemas, and audit/receipt state are all
    # enforcement-governing → MODIFY is always Quadrant D.
    for path in ("governance/scripts/classify_action.py",
                 "schemas/mirror_paths.json",
                 "bot-state/classifier_audit.jsonl"):
        pr = pr_factory(files_changed=(
            FileChange(path=path, status="modified", additions=1, deletions=0),
        ))
        assert PathDefaultClassifier().evaluate(pr).quadrant == "D", path


def test_path_default_config_deletion_is_d(pr_factory):
    # Deleting a config file is also Quadrant D (always-D path).
    pr = pr_factory(files_changed=(
        FileChange(path="config/skills.yml", status="removed",
                   additions=0, deletions=10),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "D"


def test_path_default_existing_adr_modify_is_d_but_new_adr_is_not(pr_factory):
    # A1 Tier-2 append-only: modifying an EXISTING ADR/meeting record is Quadrant
    # D (history must not be rewritten), but ADDING a new record is legitimate
    # (not D — falls through to the normal A/B path).
    modify = pr_factory(files_changed=(
        FileChange(path="docs/decisions/0007_x.md",
                   status="modified", additions=3, deletions=1),
    ))
    assert PathDefaultClassifier().evaluate(modify).quadrant == "D"
    add = pr_factory(files_changed=(
        FileChange(path="docs/decisions/0042_new.md",
                   status="added", additions=20, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(add).quadrant != "D"


def test_path_default_plain_src_modify_still_b(pr_factory):
    # Guard against over-reach: an adopter's ordinary src/ refactor is still
    # Quadrant B (critical-but-reversible), not forced to D.
    pr = pr_factory(files_changed=(
        FileChange(path="src/app/widget.py", status="modified",
                   additions=8, deletions=3),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "B"


# -- Empty PR --

def test_empty_pr_classifier_d(pr_factory):
    pr = pr_factory(files_changed=())
    assert EmptyPrClassifier().evaluate(pr).quadrant == "D"


def test_empty_pr_classifier_a_when_non_empty(pr_factory):
    pr = pr_factory(files_changed=(
        FileChange(path="README.md", status="modified", additions=1, deletions=0),
    ))
    assert EmptyPrClassifier().evaluate(pr).quadrant == "A"


# -- Bot self repo --

def test_bot_self_repo_d_when_match(pr_factory):
    # PR targets example/repo; classifier knows that's the bot repo.
    pr = pr_factory()
    v = BotSelfRepoClassifier(bot_repo_full_name="example/repo")
    assert v.evaluate(pr).quadrant == "D"


def test_bot_self_repo_a_when_no_match(pr_factory):
    pr = pr_factory()
    v = BotSelfRepoClassifier(bot_repo_full_name="other-owner/other-repo")
    assert v.evaluate(pr).quadrant == "A"


def test_bot_self_repo_a_when_not_configured(pr_factory):
    pr = pr_factory()
    v = BotSelfRepoClassifier()
    assert v.evaluate(pr).quadrant == "A"


def test_path_default_rename_out_of_governed_dir_is_d(pr_factory):
    # A1 rename-out (GPT-5.5): renaming a file OUT of a governed dir to a benign
    # path must still be Quadrant D — checked via previous_filename, so it cannot
    # escape by being classified on the new name alone.
    pr = pr_factory(files_changed=(
        FileChange(path="docs/notes.md", status="renamed", additions=0,
                   deletions=0, previous_filename="config/projects.yml"),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "D"


def test_path_default_github_scripts_modify_is_d(pr_factory):
    # A1 (GPT-5.5): .github/scripts/* are executed BY the CI workflows, so they
    # are enforcement-governing — the always-D prefix is the whole .github/ dir.
    pr = pr_factory(files_changed=(
        FileChange(path=".github/scripts/scan_no_personal_data.py",
                   status="modified", additions=2, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "D"


def test_path_default_github_actions_dir_is_d_but_issue_template_is_not(pr_factory):
    # A1 (GPT-5.5 #4): the always-D .github surface is the CI-EXECUTABLE set
    # (workflows/scripts/actions), NOT all of .github/. A composite action → D;
    # a benign ISSUE_TEMPLATE edit is NOT force-routed to D (no over-reach /
    # doctrine drift).
    action = pr_factory(files_changed=(
        FileChange(path=".github/actions/setup/action.yml",
                   status="modified", additions=1, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(action).quadrant == "D"
    tmpl = pr_factory(files_changed=(
        FileChange(path=".github/ISSUE_TEMPLATE/bug.md",
                   status="modified", additions=1, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(tmpl).quadrant != "D"
