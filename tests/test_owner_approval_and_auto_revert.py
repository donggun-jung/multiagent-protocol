"""Tests for the validator_owner_approval and classifier_auto_revert built-ins.

These two skills were promised in ``docs/concepts/skills-plugin.md`` but
absent from the codebase before R2. The tests below pin the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

from multiagent_protocol.label_provenance import (
    REBINDABLE_LABELS,
    RECEIPT_ELIGIBLE_LABELS,
    effective_label_applier,
    establishing_labeled_event,
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


# -- A3: HMAC-signed receipts (MERGE_GATE_RECEIPT_KEY) ------------------------
#
# With the key set, a receipt counts ONLY if it carries a valid MAC over
# (repo_full_name, pr_number, label, head_sha). A leaked App token can post a
# bot-authored receipt comment but cannot mint a correct MAC. Unset key →
# author-only fallback (unchanged).

from multiagent_protocol.label_provenance import (  # noqa: E402
    approval_receipt_comment,
    approval_receipts,
)

A3_REPO = "example/repo"
A3_PR = 42


def _bot_comment(body):
    return {"user": {"login": BOT}, "body": body}


def test_signed_receipt_honored_when_key_set(monkeypatch):
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", "k")
    body = approval_receipt_comment(
        "decision:approved-A", "h" * 40, repo_full_name=A3_REPO, pr_number=A3_PR)
    parsed = approval_receipts(
        [_bot_comment(body)], BOT, repo_full_name=A3_REPO, pr_number=A3_PR)
    assert parsed == {"decision:approved-A": "h" * 40}


def test_unsigned_receipt_rejected_when_key_set(monkeypatch):
    # A bot-authored receipt with NO MAC marker (the forge: a leaked token
    # lacks the key) does not count under a set key.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", "k")
    forged = approval_receipt_comment("decision:approved-A", "h" * 40)  # no ctx → no MAC
    assert approval_receipts(
        [_bot_comment(forged)], BOT, repo_full_name=A3_REPO, pr_number=A3_PR) == {}


def test_receipt_with_wrong_mac_rejected_when_key_set(monkeypatch):
    # A receipt signed under a DIFFERENT key (attacker's own MAC) is rejected.
    # Build the body under the attacker key, then verify under the real key.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", "attacker-key")
    forged = approval_receipt_comment(
        "decision:approved-A", "h" * 40, repo_full_name=A3_REPO, pr_number=A3_PR)
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", "real-key")
    assert approval_receipts(
        [_bot_comment(forged)], BOT, repo_full_name=A3_REPO, pr_number=A3_PR) == {}


def test_signed_receipt_not_replayable_to_other_pr(monkeypatch):
    # A valid receipt for pr_number=42 does not count for pr_number=99.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", "k")
    body = approval_receipt_comment(
        "decision:approved-A", "h" * 40, repo_full_name=A3_REPO, pr_number=42)
    assert approval_receipts(
        [_bot_comment(body)], BOT, repo_full_name=A3_REPO, pr_number=99) == {}


def test_unsigned_receipt_honored_when_key_unset(monkeypatch):
    # Fallback: with no key, the unsigned receipt is honoured author-only,
    # exactly as before (this is what the rest of the suite already pins).
    monkeypatch.delenv("MERGE_GATE_RECEIPT_KEY", raising=False)
    body = approval_receipt_comment("decision:approved-A", "h" * 40)
    assert approval_receipts([_bot_comment(body)], BOT) == {"decision:approved-A": "h" * 40}


def test_signed_receipt_honored_through_has_verified_label(pr_factory, monkeypatch):
    # End-to-end through the gate predicate: a signed receipt for the current
    # head, fed via approval_receipts → has_verified_label, opens C3.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", "k")
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha="h" * 40,
        commits=(_commit(),),
        label_events=(_approval_event(actor=BOT),),
    )
    body = approval_receipt_comment(
        "decision:approved-A", "h" * 40,
        repo_full_name=pr.full_name, pr_number=pr.number)
    approved = approval_receipts(
        [_bot_comment(body)], BOT,
        repo_full_name=pr.full_name, pr_number=pr.number)
    assert has_verified_label(
        pr, ("decision:approved-A",), OWNER, BOT, approved_shas=approved)


def test_forged_receipt_blocks_has_verified_label_when_key_set(pr_factory, monkeypatch):
    # The forge with no MAC: approval_receipts drops it → empty map → C3 not
    # opened (the Quadrant-D PR would route to the inbox in process_pr).
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", "k")
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha="h" * 40,
        commits=(_commit(),),
        label_events=(_approval_event(actor=BOT),),
    )
    forged = approval_receipt_comment("decision:approved-A", "h" * 40)  # no MAC
    approved = approval_receipts(
        [_bot_comment(forged)], BOT,
        repo_full_name=pr.full_name, pr_number=pr.number)
    assert approved == {}
    assert not has_verified_label(
        pr, ("decision:approved-A",), OWNER, BOT, approved_shas=approved)


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


# -- ITEM 1: effective applier = most recent labeled AFTER most recent unlabeled
#
# Provenance bypass (GPT-5.5 confirmed): a label applied by a TRUSTED actor,
# REMOVED, then RE-ADDED by an UNTRUSTED actor was still authenticated by the
# stale earlier trusted ``labeled`` event. The establishing-applier rule (the
# most recent ``labeled`` after the most recent ``unlabeled``) closes it for
# both the gate (has_verified_label / auto-revert / C1) and the receipt writer.

def _unlabel_event(label, actor="owner", at="2026-05-25T00:01:00Z"):
    return LabelEvent(label=label, actor_login=actor, created_at=at)


def test_establishing_event_picks_latest_labeled_after_unlabel():
    # trusted-add(00:00) → unlabeled(00:01) → untrusted re-add(00:02): the
    # establishing event is the untrusted re-add, not the stale trusted add.
    labeled = (
        _approval_event(actor="owner", at="2026-05-25T00:00:00Z"),
        _approval_event(actor="mallory", at="2026-05-25T00:02:00Z"),
    )
    unlabel = (_unlabel_event("decision:approved-A", at="2026-05-25T00:01:00Z"),)
    ev = establishing_labeled_event("decision:approved-A", labeled, unlabel)
    assert ev is not None and ev.actor_login == "mallory"
    assert effective_label_applier("decision:approved-A", labeled, unlabel) == "mallory"


def test_establishing_event_no_unlabel_is_latest_labeled():
    # No removal → the most recent labeled (timeline order) establishes it;
    # the plain single-add case is unchanged.
    labeled = (_approval_event(actor="owner", at="2026-05-25T00:00:00Z"),)
    assert effective_label_applier("decision:approved-A", labeled, ()) == "owner"


def test_establishing_event_fails_closed_when_only_stale_labeled_predates_removal():
    # The only labeled add predates the latest removal and there is no add
    # after it → no determinable establisher (fail closed → None).
    labeled = (_approval_event(actor="owner", at="2026-05-25T00:00:00Z"),)
    unlabel = (_unlabel_event("decision:approved-A", at="2026-05-25T00:05:00Z"),)
    assert establishing_labeled_event("decision:approved-A", labeled, unlabel) is None


def test_has_verified_label_rejects_untrusted_readd_with_matching_receipt(pr_factory):
    # THE bypass: a bot receipt still SHA-matches the (unchanged) head, the
    # original add was by the owner, but the CURRENT presence was established
    # by an untrusted re-add after a removal → C3 must NOT honour it.
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha="h" * 40,
        commits=(_commit(),),
        label_events=(
            _approval_event(actor="owner", at="2026-05-25T00:00:00Z"),
            _approval_event(actor="mallory", at="2026-05-25T00:02:00Z"),
        ),
        unlabel_events=(_unlabel_event("decision:approved-A", at="2026-05-25T00:01:00Z"),),
    )
    assert not has_verified_label(
        pr, ("decision:approved-A",), OWNER, BOT,
        approved_shas={"decision:approved-A": "h" * 40},
    )


def test_has_verified_label_honours_trusted_readd_with_matching_receipt(pr_factory):
    # trusted-add → unlabeled → re-add by a TRUSTED actor (here the bot) at the
    # current head, with a matching receipt → honoured (control for the case
    # above; only the establishing actor differs).
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha="h" * 40,
        commits=(_commit(),),
        label_events=(
            _approval_event(actor="owner", at="2026-05-25T00:00:00Z"),
            _approval_event(actor=BOT, at="2026-05-25T00:02:00Z"),
        ),
        unlabel_events=(_unlabel_event("decision:approved-A", at="2026-05-25T00:01:00Z"),),
    )
    assert has_verified_label(
        pr, ("decision:approved-A",), OWNER, BOT,
        approved_shas={"decision:approved-A": "h" * 40},
    )


def test_writer_no_receipt_for_untrusted_readd_after_removal(pr_factory):
    # Receipt writer: trusted-add → unlabeled → untrusted re-add. The stale
    # trusted add must NOT mint a receipt for the untrusted re-add → no receipt.
    pr = pr_factory(
        labels=("decision:approved-A", "ready-to-merge"), head_sha="h" * 40,
        commits=(_commit(),),
        label_events=(
            _approval_event("decision:approved-A", actor="owner", at="2026-05-25T00:00:00Z"),
            _approval_event("decision:approved-A", actor="mallory", at="2026-05-25T00:02:00Z"),
            _approval_event("ready-to-merge", actor="owner", at="2026-05-25T00:00:00Z"),
            _approval_event("ready-to-merge", actor="mallory", at="2026-05-25T00:02:00Z"),
        ),
        unlabel_events=(
            _unlabel_event("decision:approved-A", at="2026-05-25T00:01:00Z"),
            _unlabel_event("ready-to-merge", at="2026-05-25T00:01:00Z"),
        ),
    )
    assert labels_needing_receipt(pr, OWNER, BOT, approved_shas={}) == ()


def test_writer_binds_trusted_readd_after_removal(pr_factory):
    # Control: trusted-add → unlabeled → TRUSTED re-add → the writer mints a
    # first receipt (the establishing applier is trusted).
    pr = pr_factory(
        labels=("decision:approved-A",), head_sha="h" * 40,
        commits=(_commit(),),
        label_events=(
            _approval_event(actor="owner", at="2026-05-25T00:00:00Z"),
            _approval_event(actor="owner", at="2026-05-25T00:02:00Z"),
        ),
        unlabel_events=(_unlabel_event("decision:approved-A", at="2026-05-25T00:01:00Z"),),
    )
    assert labels_needing_receipt(pr, OWNER, BOT, approved_shas={}) == (
        "decision:approved-A",
    )


def test_auto_revert_rejects_untrusted_readd_after_removal(pr_factory):
    # The auto-revert door uses has_verified_label too: an untrusted re-add
    # after a removal is not honoured (votes A, not C), even with a matching
    # receipt.
    pr = pr_factory(
        labels=("decision:auto-revert",), head_sha="h" * 40,
        commits=(_commit(),),
        label_events=(
            _approval_event("decision:auto-revert", actor="owner", at="2026-05-25T00:00:00Z"),
            _approval_event("decision:auto-revert", actor="mallory", at="2026-05-25T00:02:00Z"),
        ),
        unlabel_events=(_unlabel_event("decision:auto-revert", at="2026-05-25T00:01:00Z"),),
    )
    v = AutoRevertClassifier(
        allowlisted_actors=OWNER, bot_user=BOT,
        approved_shas={"decision:auto-revert": "h" * 40},
    ).evaluate(pr)
    assert v.quadrant == "A"


def test_no_receipt_for_bot_applied_label_laundering_guard(pr_factory):
    # A3 laundering (GPT-5.5): a gate-opening label applied AS THE BOT (e.g. via
    # a leaked App token) must NOT be self-minted into a valid receipt — only an
    # owner-allowlisted (human) applier mints a first-sight receipt here. The
    # bot's legitimate approved-* receipts come from the Decision Inbox path.
    pr = _hand_applied_pr(
        pr_factory, ("decision:approved-A", "ready-to-merge"), actor=BOT)
    assert labels_needing_receipt(pr, OWNER, BOT, approved_shas={}) == ()
