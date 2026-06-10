"""Tests for the validator_owner_approval and classifier_auto_revert built-ins.

These two skills were promised in ``docs/concepts/skills-plugin.md`` but
absent from the codebase before R2. The tests below pin the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

from multiagent_protocol.label_provenance import has_verified_label
from multiagent_protocol.skills.builtin.classifier_auto_revert import (
    AutoRevertClassifier,
)
from multiagent_protocol.skills.builtin.validator_owner_approval import (
    OwnerApprovalValidator,
)
from multiagent_protocol.types import CommitContext, LabelEvent, TrailerSet

OWNER = ("owner",)
BOT = "my-bot[bot]"
SHA1 = "1" * 40
SHA2 = "2" * 40


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


def test_owner_approval_approved_c_label_does_not_satisfy_c3(pr_factory):
    # Option C = "defer / needs more info" (Decision Inbox doctrine) — it must
    # NEVER grant a merge, even when applied fresh by the owner themselves.
    pr = pr_factory(
        labels=("decision:approved-C",), commits=(_commit(),),
        label_events=(_approval_event("decision:approved-C"),),
    )
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


# -- Owner approval: SHA-bound receipts (vNext hardening) --

def test_owner_approval_rejects_receipt_bound_to_old_head_even_if_time_fresh(pr_factory):
    # THE backdating exploit the SHA binding closes: the bot recorded the
    # approval (receipt) at SHA1; the agent then pushes SHA2 with a BACKDATED
    # committer date (00:00 — BEFORE the 00:05 approval event, i.e. the head
    # commit predates the approval). The time-only check would honour the
    # stale approval; the receipt says SHA1 != head SHA2 → void.
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha=SHA2,
        commits=(
            _commit(sha=SHA1, date="2026-05-25T00:00:00Z"),
            _commit(sha=SHA2, date="2026-05-25T00:00:00Z"),  # backdated head
        ),
        label_events=(_approval_event(actor=BOT, at="2026-05-25T00:05:00Z"),),
    )
    v = OwnerApprovalValidator(
        classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={"decision:approved-A": SHA1},
    )
    assert not v.check(pr).passed


def test_owner_approval_passes_when_reapproved_at_new_head(pr_factory):
    # Re-approval at SHA2 (receipt now binds to the current head) → valid.
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha=SHA2,
        commits=(_commit(sha=SHA2, date="2026-05-25T00:00:00Z"),),
        label_events=(_approval_event(actor=BOT, at="2026-05-25T00:05:00Z"),),
    )
    v = OwnerApprovalValidator(
        classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={"decision:approved-A": SHA2},
    )
    assert v.check(pr).passed


def test_owner_approval_receipt_does_not_bless_untrusted_applier(pr_factory):
    # Defense-in-depth ordering: even with a receipt matching the current
    # head, the labeled event's actor must still be the owner or the bot.
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha=SHA2,
        commits=(_commit(sha=SHA2, date="2026-05-25T00:00:00Z"),),
        label_events=(_approval_event(actor="mallory", at="2026-05-25T00:05:00Z"),),
    )
    v = OwnerApprovalValidator(
        classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={"decision:approved-A": SHA2},
    )
    assert not v.check(pr).passed


# -- Time-path extra safety: head committer-date sanity (vNext hardening) --

def test_time_path_fails_closed_on_future_head_date(pr_factory):
    # No receipt; the head committer date is in the future (beyond clock
    # skew). The event is "at/after head"-satisfiable only because the date
    # is garbage → treat as unverifiable → fail closed.
    now = datetime(2026, 5, 25, 0, 10, tzinfo=timezone.utc)
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha="h" * 40,
        commits=(_commit(date="2026-05-25T12:00:00Z"),),
        label_events=(_approval_event(at="2026-05-25T13:00:00Z"),),
    )
    assert not has_verified_label(
        pr, ("decision:approved-A",), OWNER, BOT, now=now
    )


def test_time_path_fails_closed_on_implausibly_old_head_date(pr_factory):
    # No receipt; the head committer date is decades in the past (garbage /
    # epoch-style backdating) → unverifiable → fail closed.
    now = datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc)
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha="h" * 40,
        commits=(_commit(date="2001-01-01T00:00:00Z"),),
        label_events=(_approval_event(at="2026-05-25T00:05:00Z"),),
    )
    assert not has_verified_label(
        pr, ("decision:approved-A",), OWNER, BOT, now=now
    )


def test_time_path_fails_closed_on_unparseable_head_date(pr_factory):
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha="h" * 40,
        commits=(_commit(date="not-a-date"),),
        label_events=(_approval_event(at="2026-05-25T00:05:00Z"),),
    )
    assert not has_verified_label(pr, ("decision:approved-A",), OWNER, BOT)


def test_receipt_binding_is_date_independent(pr_factory):
    # A receipt matching the current head holds even when the head committer
    # date is garbage — the binding is by SHA, not by time.
    now = datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc)
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha=SHA2,
        commits=(_commit(sha=SHA2, date="2001-01-01T00:00:00Z"),),
        label_events=(_approval_event(actor=BOT, at="2026-05-25T00:05:00Z"),),
    )
    assert has_verified_label(
        pr, ("decision:approved-A",), OWNER, BOT,
        approved_shas={"decision:approved-A": SHA2}, now=now,
    )


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


def test_auto_revert_votes_a_when_receipt_bound_to_old_head(pr_factory):
    # SHA binding (vNext): the bot recorded the label at SHA1; head moved to
    # SHA2 (backdated so the time check alone would pass) → not honoured.
    pr = pr_factory(
        labels=("decision:auto-revert",), head_sha=SHA2,
        commits=(_commit(sha=SHA2, date="2026-05-25T00:00:00Z"),),
        label_events=(
            _approval_event("decision:auto-revert", actor=BOT, at="2026-05-25T00:05:00Z"),
        ),
    )
    v = AutoRevertClassifier(
        allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={"decision:auto-revert": SHA1},
    ).evaluate(pr)
    assert v.quadrant == "A"


def test_auto_revert_votes_c_when_receipt_matches_head(pr_factory):
    pr = pr_factory(
        labels=("decision:auto-revert",), head_sha=SHA2,
        commits=(_commit(sha=SHA2, date="2026-05-25T00:00:00Z"),),
        label_events=(
            _approval_event("decision:auto-revert", actor=BOT, at="2026-05-25T00:05:00Z"),
        ),
    )
    v = AutoRevertClassifier(
        allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={"decision:auto-revert": SHA2},
    ).evaluate(pr)
    assert v.quadrant == "C"
