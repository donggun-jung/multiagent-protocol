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
from multiagent_protocol.types import CommitContext, LabelEvent, TrailerSet

OWNER = ("owner",)
BOT = "my-bot[bot]"


def _approval_event(label="decision:approved-A", actor="owner", at="2026-05-25T00:00:00Z"):
    return LabelEvent(label=label, actor_login=actor, created_at=at)


def _commit(sha="h" * 40, date="2026-05-25T00:00:00Z"):
    return CommitContext(
        sha=sha, subject="x", body="", author_login="a", committer_login="a",
        parents=(), trailers=TrailerSet(), committed_at=date,
    )


# -- Owner approval: auto-approval path (A/B/C) --

def test_owner_approval_passes_when_classifier_a(pr_factory):
    assert OwnerApprovalValidator(classifier_verdict="A").check(pr_factory()).passed


def test_owner_approval_passes_when_classifier_b(pr_factory):
    assert OwnerApprovalValidator(classifier_verdict="B").check(pr_factory()).passed


def test_owner_approval_passes_when_classifier_c(pr_factory):
    assert OwnerApprovalValidator(classifier_verdict="C").check(pr_factory()).passed


# -- Owner approval: Quadrant-D label path (verified) --

def test_owner_approval_passes_with_owner_applied_label(pr_factory):
    pr = pr_factory(
        labels=("decision:approved-A",), commits=(_commit(),),
        label_events=(_approval_event(),),
    )
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert v.check(pr).passed


def test_owner_approval_passes_with_bot_applied_label(pr_factory):
    pr = pr_factory(
        labels=("decision:approved-B",), commits=(_commit(),),
        label_events=(_approval_event("decision:approved-B", actor=BOT),),
    )
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert v.check(pr).passed


def test_owner_approval_fails_closed_when_head_date_unknown(pr_factory):
    # Head commit carries no date → freshness unverifiable → fail closed.
    head = CommitContext(sha="h" * 40, subject="x", body="", author_login="a",
                         committer_login="a", parents=(), trailers=TrailerSet(),
                         committed_at=None)
    pr = pr_factory(labels=("decision:approved-A",), head_sha="h" * 40,
                    commits=(head,), label_events=(_approval_event(),))
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert not v.check(pr).passed


def test_owner_approval_fails_closed_when_event_timestamp_empty(pr_factory):
    # Approval event with empty created_at → fail closed (cannot prove fresh).
    pr = pr_factory(labels=("decision:approved-A",), head_sha="h" * 40,
                    commits=(_commit(),), label_events=(_approval_event(at=""),))
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert not v.check(pr).passed


def test_owner_approval_fails_closed_when_head_sha_absent_from_commits(pr_factory):
    # Head SHA not among the PR's commits → no max() fallback → fail closed.
    other = _commit(sha="z" * 40, date="2026-05-25T00:00:00Z")
    pr = pr_factory(labels=("decision:approved-A",), head_sha="h" * 40,
                    commits=(other,), label_events=(_approval_event(at="2026-05-25T09:00:00Z"),))
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert not v.check(pr).passed


def test_owner_approval_rejects_self_applied_label(pr_factory):
    # Exploit A: a non-allowlisted collaborator self-applies the approval label.
    pr = pr_factory(
        labels=("decision:approved-A",),
        label_events=(_approval_event(actor="mallory"),),
    )
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert not v.check(pr).passed


def test_owner_approval_rejects_stale_label_after_forcepush(pr_factory):
    # Exploit B: approval applied at 00:00, then a force-push lands a head
    # commit at 00:05 → the approval predates the current head → void.
    pr = pr_factory(
        labels=("decision:approved-A",),
        head_sha="h" * 40,
        commits=(_commit(date="2026-05-25T00:05:00Z"),),
        label_events=(_approval_event(at="2026-05-25T00:00:00Z"),),
    )
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert not v.check(pr).passed


def test_owner_approval_passes_when_approval_after_head(pr_factory):
    # Approval applied at 00:05, head commit at 00:00 → fresh → valid.
    pr = pr_factory(
        labels=("decision:approved-A",),
        head_sha="h" * 40,
        commits=(_commit(date="2026-05-25T00:00:00Z"),),
        label_events=(_approval_event(at="2026-05-25T00:05:00Z"),),
    )
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert v.check(pr).passed


def test_owner_approval_rejects_label_present_without_event(pr_factory):
    # Label present but no timeline event for it → cannot verify applier → fail.
    pr = pr_factory(labels=("decision:approved-A",), label_events=())
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert not v.check(pr).passed


def test_owner_approval_rejects_event_without_current_label(pr_factory):
    # Event exists but the label was removed since (not currently present).
    pr = pr_factory(labels=(), label_events=(_approval_event(),))
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert not v.check(pr).passed


def test_owner_approval_fails_quadrant_d_no_approval(pr_factory):
    r = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER).check(pr_factory(labels=()))
    assert not r.passed
    assert "C3" in r.failure_reason


def test_owner_approval_fails_when_classifier_unknown_no_label(pr_factory):
    r = OwnerApprovalValidator(classifier_verdict=None, allowlisted_actors=OWNER).check(pr_factory(labels=()))
    assert not r.passed
    assert "unknown" in r.failure_reason


def test_owner_approval_ignores_unrelated_labels(pr_factory):
    pr = pr_factory(labels=("ready-to-merge", "documentation"))
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER)
    assert not v.check(pr).passed


# -- Auto-revert classifier (provenance-checked, like C3) --

def test_auto_revert_votes_c_when_owner_applied_fresh(pr_factory):
    pr = pr_factory(
        labels=("decision:auto-revert",), commits=(_commit(),),
        label_events=(_approval_event("decision:auto-revert", actor="owner"),),
    )
    v = AutoRevertClassifier(allowlisted_actors=OWNER, bot_user=BOT).evaluate(pr)
    assert v.quadrant == "C"


def test_auto_revert_votes_a_when_label_absent(pr_factory):
    pr = pr_factory(labels=("documentation",))
    v = AutoRevertClassifier(allowlisted_actors=OWNER, bot_user=BOT).evaluate(pr)
    assert v.quadrant == "A"


def test_auto_revert_votes_a_when_self_applied(pr_factory):
    # A non-allowlisted actor self-applies the label → not honoured.
    pr = pr_factory(
        labels=("decision:auto-revert",), commits=(_commit(),),
        label_events=(_approval_event("decision:auto-revert", actor="mallory"),),
    )
    v = AutoRevertClassifier(allowlisted_actors=OWNER, bot_user=BOT).evaluate(pr)
    assert v.quadrant == "A"


def test_auto_revert_votes_a_when_stale(pr_factory):
    # Applied before the current head (force-push) → voided.
    pr = pr_factory(
        labels=("decision:auto-revert",), head_sha="h" * 40,
        commits=(_commit(date="2026-05-25T00:05:00Z"),),
        label_events=(_approval_event("decision:auto-revert", at="2026-05-25T00:00:00Z"),),
    )
    v = AutoRevertClassifier(allowlisted_actors=OWNER, bot_user=BOT).evaluate(pr)
    assert v.quadrant == "A"


def test_auto_revert_zero_arg_is_noop(pr_factory):
    # The loader's 0-arg instance trusts nobody → always votes A.
    pr = pr_factory(
        labels=("decision:auto-revert",), commits=(_commit(),),
        label_events=(_approval_event("decision:auto-revert", actor="owner"),),
    )
    assert AutoRevertClassifier().evaluate(pr).quadrant == "A"
