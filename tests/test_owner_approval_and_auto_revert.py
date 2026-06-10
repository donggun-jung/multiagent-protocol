"""Tests for the validator_owner_approval and classifier_auto_revert built-ins.

These two skills were promised in ``docs/concepts/skills-plugin.md`` but
absent from the codebase before R2. The tests below pin the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

from multiagent_protocol.label_provenance import (
    REBINDABLE_LABELS,
    RECEIPT_ELIGIBLE_LABELS,
    has_verified_label,
    labels_needing_receipt,
)
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
#
# Receipt-required contract (vNext hardening): an approval label opens C3
# ONLY together with the bot's SHA receipt binding it to the current head.
# These two positive tests therefore carry a matching receipt — the old
# receipt-less variants relied on the removed committer-date fallback.

def test_owner_approval_passes_with_owner_applied_label(pr_factory):
    pr = pr_factory(
        labels=("decision:approved-A",), commits=(_commit(),),
        label_events=(_approval_event(),),
    )
    v = OwnerApprovalValidator(
        classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={"decision:approved-A": "h" * 40},
    )
    assert v.check(pr).passed


def test_owner_approval_passes_with_bot_applied_label(pr_factory):
    pr = pr_factory(
        labels=("decision:approved-B",), commits=(_commit(),),
        label_events=(_approval_event("decision:approved-B", actor=BOT),),
    )
    v = OwnerApprovalValidator(
        classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={"decision:approved-B": "h" * 40},
    )
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


def test_owner_approval_without_receipt_not_honored_even_when_time_fresh(pr_factory):
    # THE case that previously slipped through: no receipt, and the head's
    # committer date (00:00) is BACKDATED to before the approval event
    # (00:05), so the old time-based fallback saw a "fresh" approval and
    # honoured it. Committer dates are client-supplied — a force-pushed head
    # can always be backdated. The receipt-required contract closes this:
    # an approval label with no receipt is NEVER honoured, dates be damned.
    pr = pr_factory(
        labels=("decision:approved-A",),
        head_sha="h" * 40,
        commits=(_commit(date="2026-05-25T00:00:00Z"),),
        label_events=(_approval_event(at="2026-05-25T00:05:00Z"),),
    )
    v = OwnerApprovalValidator(classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT)
    assert not v.check(pr).passed
    # Same with an explicitly empty receipt map (the runtime always passes one).
    v2 = OwnerApprovalValidator(
        classifier_verdict="D", allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={},
    )
    assert not v2.check(pr).passed


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
#
# The time-based path now serves ONLY non-receipt labels (decision:auto-revert);
# an approval label short-circuits on the missing receipt before any date is
# read. These tests therefore exercise the auto-revert label so the head-date
# sanity checks stay covered on the path that actually runs them.

def test_time_path_fails_closed_on_future_head_date(pr_factory):
    # No receipt; the head committer date is in the future (beyond clock
    # skew). The event is "at/after head"-satisfiable only because the date
    # is garbage → treat as unverifiable → fail closed.
    now = datetime(2026, 5, 25, 0, 10, tzinfo=timezone.utc)
    pr = pr_factory(
        labels=("decision:auto-revert",), head_sha="h" * 40,
        commits=(_commit(date="2026-05-25T12:00:00Z"),),
        label_events=(_approval_event("decision:auto-revert", at="2026-05-25T13:00:00Z"),),
    )
    assert not has_verified_label(
        pr, ("decision:auto-revert",), OWNER, BOT, now=now
    )


def test_time_path_fails_closed_on_implausibly_old_head_date(pr_factory):
    # No receipt; the head committer date is decades in the past (garbage /
    # epoch-style backdating) → unverifiable → fail closed.
    now = datetime(2026, 5, 25, 0, 0, tzinfo=timezone.utc)
    pr = pr_factory(
        labels=("decision:auto-revert",), head_sha="h" * 40,
        commits=(_commit(date="2001-01-01T00:00:00Z"),),
        label_events=(_approval_event("decision:auto-revert", at="2026-05-25T00:05:00Z"),),
    )
    assert not has_verified_label(
        pr, ("decision:auto-revert",), OWNER, BOT, now=now
    )


def test_time_path_fails_closed_on_unparseable_head_date(pr_factory):
    pr = pr_factory(
        labels=("decision:auto-revert",), head_sha="h" * 40,
        commits=(_commit(date="not-a-date"),),
        label_events=(_approval_event("decision:auto-revert", at="2026-05-25T00:05:00Z"),),
    )
    assert not has_verified_label(pr, ("decision:auto-revert",), OWNER, BOT)


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


# -- Timestamp parsing (vNext hardening): strict ISO-8601, fail closed --

def test_plausible_head_date_accepts_iso8601_variants():
    from multiagent_protocol.label_provenance import plausible_head_date
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    assert plausible_head_date("2026-05-25T00:00:00Z", now)          # trailing Z
    assert plausible_head_date("2026-05-25T00:00:00+00:00", now)     # numeric offset
    assert plausible_head_date("2026-05-25T00:00:00.123Z", now)      # fractional seconds
    assert plausible_head_date("2026-05-25T09:00:00+09:00", now)     # non-UTC offset
    assert plausible_head_date("2026-05-25T00:00:00", now)           # naive → UTC


def test_plausible_head_date_unparseable_fails_closed():
    from multiagent_protocol.label_provenance import plausible_head_date
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    assert not plausible_head_date("not-a-date", now)
    assert not plausible_head_date("", now)
    assert not plausible_head_date("2026-13-45T99:99:99Z", now)      # invalid fields
    assert not plausible_head_date("1748131200", now)                # epoch seconds, not ISO


def test_time_path_fails_closed_on_unparseable_event_timestamp(pr_factory):
    # The label-event timestamp (not just the head date) is parsed; a garbage
    # event time can never satisfy the at-or-after-head comparison. Uses the
    # auto-revert label — the one label door still served by the time path.
    pr = pr_factory(
        labels=("decision:auto-revert",), head_sha="h" * 40,
        commits=(_commit(date="2026-05-25T00:00:00Z"),),
        label_events=(
            _approval_event("decision:auto-revert", at="garbage-timestamp"),
        ),
    )
    assert not has_verified_label(pr, ("decision:auto-revert",), OWNER, BOT)


# -- Receipt writer decision: labels_needing_receipt (vNext hardening) --

def test_receipt_eligible_set_matches_validator_constants():
    # The literals in label_provenance must track the validators' constants.
    from multiagent_protocol.skills.builtin.validator_owner_approval import (
        APPROVAL_LABELS,
    )
    from multiagent_protocol.skills.builtin.validator_ready_to_merge import (
        READY_LABEL,
    )
    assert RECEIPT_ELIGIBLE_LABELS == set(APPROVAL_LABELS) | {READY_LABEL}
    assert REBINDABLE_LABELS == {READY_LABEL}
    assert REBINDABLE_LABELS <= RECEIPT_ELIGIBLE_LABELS


def _hand_applied_pr(pr_factory, labels, *, actor="owner",
                     head_date="2026-05-25T00:00:00Z",
                     event_at="2026-05-25T00:05:00Z"):
    return pr_factory(
        labels=labels, head_sha="h" * 40,
        commits=(_commit(date=head_date),),
        label_events=tuple(
            _approval_event(lb, actor=actor, at=event_at) for lb in labels
        ),
    )


def test_first_bind_for_fresh_allowlisted_labels(pr_factory):
    pr = _hand_applied_pr(pr_factory, ("decision:approved-A", "ready-to-merge"))
    out = labels_needing_receipt(pr, OWNER, BOT, approved_shas={})
    assert out == ("decision:approved-A", "ready-to-merge")  # sorted


def test_no_receipt_for_non_allowlisted_applier(pr_factory):
    pr = _hand_applied_pr(
        pr_factory, ("decision:approved-A", "ready-to-merge"), actor="mallory")
    assert labels_needing_receipt(pr, OWNER, BOT, approved_shas={}) == ()


def test_first_bind_ignores_committer_date_when_event_predates_head(pr_factory):
    # vNext: the WRITER no longer consults the head's committer date. A commit
    # date is client-supplied and forgeable, so "label event before the head
    # commit date" is NOT a freshness signal: an attacker can always backdate
    # a force-pushed head. The first receipt binds to the head the bot
    # observes here regardless of the head date — the residual is a force-push
    # before the bot's FIRST observation (documented; airtight path = inbox).
    pr = _hand_applied_pr(
        pr_factory, ("decision:approved-A",),
        head_date="2026-05-25T00:10:00Z", event_at="2026-05-25T00:00:00Z")
    assert labels_needing_receipt(pr, OWNER, BOT, approved_shas={}) == (
        "decision:approved-A",
    )


def test_no_receipt_when_already_bound_to_current_head(pr_factory):
    pr = _hand_applied_pr(pr_factory, ("decision:approved-A",))
    assert labels_needing_receipt(
        pr, OWNER, BOT, approved_shas={"decision:approved-A": "h" * 40}) == ()


def test_stale_approval_receipt_is_never_rebound(pr_factory):
    # An approval receipt bound to an OLD head is superseded only via the
    # Decision Inbox — the writer must not auto-re-approve unreviewed code.
    pr = _hand_applied_pr(pr_factory, ("decision:approved-A",))
    assert labels_needing_receipt(
        pr, OWNER, BOT,
        approved_shas={"decision:approved-A": SHA1},
        receipt_times={"decision:approved-A": "2026-05-25T00:01:00Z"},
    ) == ()


def test_stale_ready_receipt_rebinds_on_fresh_owner_event(pr_factory):
    # ready-to-merge re-binds ONLY on proof of fresh owner intent: a trusted
    # labeled event strictly newer than the bot's last receipt comment.
    pr = _hand_applied_pr(
        pr_factory, ("ready-to-merge",), event_at="2026-05-25T00:30:00Z")
    assert labels_needing_receipt(
        pr, OWNER, BOT,
        approved_shas={"ready-to-merge": SHA1},
        receipt_times={"ready-to-merge": "2026-05-25T00:10:00Z"},
    ) == ("ready-to-merge",)


def test_stale_ready_receipt_not_rebound_without_fresh_event(pr_factory):
    # The only ready event is at/before the receipt time → no re-apply by the
    # owner since the binding → fail closed (no silent re-bind).
    pr = _hand_applied_pr(
        pr_factory, ("ready-to-merge",), event_at="2026-05-25T00:10:00Z")
    assert labels_needing_receipt(
        pr, OWNER, BOT,
        approved_shas={"ready-to-merge": SHA1},
        receipt_times={"ready-to-merge": "2026-05-25T00:10:00Z"},
    ) == ()


def test_stale_ready_receipt_not_rebound_without_receipt_time(pr_factory):
    # The receipt's comment timestamp is missing/garbage → cannot prove the
    # re-apply postdates the binding → fail closed.
    pr = _hand_applied_pr(
        pr_factory, ("ready-to-merge",), event_at="2026-05-25T00:30:00Z")
    assert labels_needing_receipt(
        pr, OWNER, BOT, approved_shas={"ready-to-merge": SHA1},
        receipt_times={},
    ) == ()
    assert labels_needing_receipt(
        pr, OWNER, BOT, approved_shas={"ready-to-merge": SHA1},
        receipt_times={"ready-to-merge": "garbage"},
    ) == ()


def test_first_bind_ignores_head_date_even_when_unparseable_or_future(pr_factory):
    # vNext: the writer ignores the head committer date entirely, so an
    # unparseable OR implausibly-future head date no longer suppresses the
    # first receipt — the binding is by observed SHA, not by time.
    bad_date = _hand_applied_pr(
        pr_factory, ("decision:approved-A",), head_date="not-a-date")
    assert labels_needing_receipt(bad_date, OWNER, BOT, approved_shas={}) == (
        "decision:approved-A",
    )
    future = _hand_applied_pr(
        pr_factory, ("decision:approved-A",),
        head_date="2026-05-25T12:00:00Z", event_at="2026-05-25T13:00:00Z")
    assert labels_needing_receipt(future, OWNER, BOT, approved_shas={}) == (
        "decision:approved-A",
    )


def test_first_bind_ignores_unparseable_event_timestamp(pr_factory):
    # First-bind needs only present + trusted applier + no receipt; it does
    # not parse the label-event timestamp (only the re-bind freshness check
    # does). A garbage event time no longer suppresses the first receipt.
    pr = _hand_applied_pr(
        pr_factory, ("decision:approved-A",), event_at="yesterday")
    assert labels_needing_receipt(pr, OWNER, BOT, approved_shas={}) == (
        "decision:approved-A",
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
