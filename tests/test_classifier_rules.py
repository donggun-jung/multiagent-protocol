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


def test_path_default_workflow_change_is_b(pr_factory):
    pr = pr_factory(files_changed=(
        FileChange(path=".github/workflows/tests.yml",
                   status="modified", additions=5, deletions=1),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "B"


def test_path_default_concept_doc_change_is_b(pr_factory):
    # docs/concepts/* is critical (changes rules) but reversible if modified
    pr = pr_factory(files_changed=(
        FileChange(path="docs/concepts/architecture.md",
                   status="modified", additions=1, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "B"


def test_path_default_config_change_is_critical_not_a(pr_factory):
    # DEC-C fix: a PR editing the gate's OWN config (e.g. config/projects.yml —
    # adding a repo to audit_only_repos, changing required_checks, the agent
    # registry) reconfigures enforcement and must be at least CRITICAL (B/D,
    # owner-visible), never auto-merged as Quadrant A.
    pr = pr_factory(files_changed=(
        FileChange(path="config/projects.yml",
                   status="modified", additions=2, deletions=0),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "B"


def test_path_default_config_deletion_is_d(pr_factory):
    # Deleting a config file is irreversible + critical → Quadrant D.
    pr = pr_factory(files_changed=(
        FileChange(path="config/skills.yml", status="removed",
                   additions=0, deletions=10),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "D"


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
