"""Tests for the validator_owner_approval and classifier_auto_revert built-ins.

These two skills were promised in ``docs/concepts/skills-plugin.md`` but
absent from the codebase before R2. The tests below pin the contract.
"""

from __future__ import annotations

from multiagent_protocol.skills.builtin.classifier_auto_revert import (
    AutoRevertClassifier,
)
from multiagent_protocol.skills.builtin.validator_owner_approval import (
    OwnerApprovalValidator,
)

# -- Owner approval --

def test_owner_approval_passes_when_classifier_a(pr_factory):
    pr = pr_factory()
    v = OwnerApprovalValidator(classifier_verdict="A")
    assert v.check(pr).passed


def test_owner_approval_passes_when_classifier_b(pr_factory):
    pr = pr_factory()
    v = OwnerApprovalValidator(classifier_verdict="B")
    assert v.check(pr).passed


def test_owner_approval_passes_when_classifier_c(pr_factory):
    pr = pr_factory()
    v = OwnerApprovalValidator(classifier_verdict="C")
    assert v.check(pr).passed


def test_owner_approval_passes_when_label_approved_a(pr_factory):
    pr = pr_factory(labels=("decision:approved-A",))
    v = OwnerApprovalValidator(classifier_verdict="D")
    assert v.check(pr).passed


def test_owner_approval_passes_when_label_approved_b(pr_factory):
    pr = pr_factory(labels=("decision:approved-B",))
    v = OwnerApprovalValidator(classifier_verdict="D")
    assert v.check(pr).passed


def test_owner_approval_passes_when_label_approved_c(pr_factory):
    pr = pr_factory(labels=("decision:approved-C",))
    v = OwnerApprovalValidator(classifier_verdict="D")
    assert v.check(pr).passed


def test_owner_approval_fails_quadrant_d_no_approval(pr_factory):
    pr = pr_factory(labels=())
    v = OwnerApprovalValidator(classifier_verdict="D")
    r = v.check(pr)
    assert not r.passed
    assert "C3" in r.failure_reason
    assert "D" in r.failure_reason


def test_owner_approval_fails_when_classifier_unknown_no_label(pr_factory):
    pr = pr_factory(labels=())
    v = OwnerApprovalValidator(classifier_verdict=None)
    r = v.check(pr)
    assert not r.passed
    assert "unknown" in r.failure_reason


def test_owner_approval_ignores_unrelated_labels(pr_factory):
    pr = pr_factory(labels=("ready-to-merge", "documentation"))
    v = OwnerApprovalValidator(classifier_verdict="D")
    assert not v.check(pr).passed


# -- Auto-revert classifier --

def test_auto_revert_classifier_votes_c(pr_factory):
    pr = pr_factory(labels=("decision:auto-revert",))
    v = AutoRevertClassifier().evaluate(pr)
    assert v.quadrant == "C"
    assert "auto-revert" in v.reasoning


def test_auto_revert_classifier_votes_a_when_label_absent(pr_factory):
    pr = pr_factory(labels=("documentation",))
    v = AutoRevertClassifier().evaluate(pr)
    assert v.quadrant == "A"
