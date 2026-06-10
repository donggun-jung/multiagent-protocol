"""Tests for hook_break_glass_audit, including the 24-hour deadline.

The pre-R2 hook had no way to enforce the deadline because CommitContext
did not expose a timestamp. After R2 the hook receives ``committed_at``
on every commit and compares against the configurable
``adr_deadline_hours``. These tests pin the contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from multiagent_protocol.skills.builtin.hook_break_glass_audit import (
    BreakGlassAuditHook,
)
from multiagent_protocol.types import CommitContext, TrailerSet

NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _commit(
    subject: str,
    *,
    author: str = "alice",
    sha: str = "a" * 40,
    committed_at: str | None = None,
) -> CommitContext:
    return CommitContext(
        sha=sha,
        subject=subject,
        body="",
        author_login=author,
        committer_login=author,
        parents=(),
        trailers=TrailerSet(),
        committed_at=committed_at,
    )


def test_non_break_glass_commits_pass():
    hook = BreakGlassAuditHook()
    r = hook.on_commit(_commit("feat: ordinary commit"))
    assert r.incident_label is None


def test_unauthorized_actor_triggers_incident():
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: True,
        clock=lambda: NOW,
    )
    r = hook.on_commit(_commit(
        "[break-glass-actions-outage] hotfix",
        author="bystander",
    ))
    assert r.incident_label == "decision:break-glass-unauthorized"
    assert "bystander" in r.incident_body


def test_authorized_actor_with_adr_passes():
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: True,
        clock=lambda: NOW,
    )
    r = hook.on_commit(_commit(
        "[break-glass-bot-self-update] fix classifier",
        author="owner",
        committed_at="2026-05-26T11:30:00Z",
    ))
    assert r.incident_label is None


def test_no_adr_within_window_does_not_alarm_yet():
    """Commit was 1 hour ago, deadline is 24 hours — still in grace."""
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: False,
        adr_deadline_hours=24,
        clock=lambda: NOW,
    )
    one_hour_ago = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = hook.on_commit(_commit(
        "[break-glass-security] revoke leaked key",
        author="owner",
        committed_at=one_hour_ago,
    ))
    assert r.incident_label is None, (
        "Within grace window the hook must not yet open the unaudited issue"
    )


def test_no_adr_past_deadline_triggers_incident():
    """Commit was 25 hours ago, deadline is 24 hours — alarm."""
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: False,
        adr_deadline_hours=24,
        clock=lambda: NOW,
    )
    past_deadline = (NOW - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = hook.on_commit(_commit(
        "[break-glass-security] revoke leaked key",
        author="owner",
        committed_at=past_deadline,
    ))
    assert r.incident_label == "decision:break-glass-unaudited"
    assert "24" in r.incident_body  # deadline cited


def test_missing_committed_at_treated_as_past_deadline():
    """Older bots that did not populate committed_at: be conservative."""
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: False,
        adr_deadline_hours=24,
        clock=lambda: NOW,
    )
    r = hook.on_commit(_commit(
        "[break-glass-actions-outage] hotfix",
        author="owner",
        committed_at=None,
    ))
    assert r.incident_label == "decision:break-glass-unaudited"


def test_committer_not_allowlisted_flagged_even_if_author_is():
    # R3 hardening (L5 actor trust): the allowlist check uses COMMITTER, not the
    # forgeable author. A [break-glass-*] commit whose AUTHOR is allowlisted
    # ('owner') but whose COMMITTER is not ('mallory') must STILL be flagged —
    # otherwise an attacker forges an allowlisted author to skip the gate.
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: True,
        clock=lambda: NOW,
    )
    commit = CommitContext(
        sha="b" * 40,
        subject="[break-glass-x] forged-author hotfix",
        body="",
        author_login="owner",        # forgeable, allowlisted
        committer_login="mallory",   # the real committer, NOT allowlisted
        parents=(),
        trailers=TrailerSet(),
        committed_at="2026-05-26T11:30:00Z",
    )
    r = hook.on_commit(commit)
    assert r.incident_label == "decision:break-glass-unauthorized"
    assert "mallory" in r.incident_body  # the committer, not the author, is named


def test_allowlisted_committer_with_forged_low_author_passes():
    # Symmetric baseline: when the COMMITTER is allowlisted, the commit passes
    # the actor check regardless of the author field.
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: True,
        clock=lambda: NOW,
    )
    commit = CommitContext(
        sha="c" * 40,
        subject="[break-glass-x] legit hotfix",
        body="",
        author_login="anyone",       # author is irrelevant to the gate
        committer_login="owner",     # allowlisted committer
        parents=(),
        trailers=TrailerSet(),
        committed_at="2026-05-26T11:30:00Z",
    )
    assert hook.on_commit(commit).incident_label is None


BOT = "acme-merge-gate[bot]"


def test_bot_self_squash_of_break_glass_titled_pr_is_no_incident():
    # Opus P2-2: a Quadrant-A PR whose TITLE starts with [break-glass-...] is
    # squash-merged by the BOT (committer = bot). L5 must NOT raise a false
    # decision:break-glass-unauthorized just because the bot is not in the human
    # allowlist — the bot's own squash is not a human break-glass push. With
    # bot_user wired, committer == bot short-circuits to no incident.
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: False,   # no ADR — would otherwise alarm
        clock=lambda: NOW,
        bot_user=BOT,
    )
    commit = CommitContext(
        sha="d" * 40,
        subject="[break-glass-actions-outage] hotfix from PR title",
        body="",
        author_login="contributor",
        committer_login=BOT,            # the bot squash-merged it
        parents=(),
        trailers=TrailerSet(),
        committed_at=None,              # even past-deadline: still no incident
    )
    assert hook.on_commit(commit).incident_label is None


def test_non_bot_committer_break_glass_no_adr_still_incident():
    # Control for P2-2: the short-circuit is committer-scoped. A NON-bot
    # committer with the break-glass subject and no ADR (past deadline) still
    # opens the unaudited incident — the bot_user wiring must not weaken L5 for
    # real (human / unknown) committers.
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: False,
        adr_deadline_hours=24,
        clock=lambda: NOW,
        bot_user=BOT,
    )
    commit = CommitContext(
        sha="e" * 40,
        subject="[break-glass-security] revoke leaked key",
        body="",
        author_login="owner",
        committer_login="owner",        # allowlisted human, but no ADR
        parents=(),
        trailers=TrailerSet(),
        committed_at=None,              # unknown ts → treated as past deadline
    )
    r = hook.on_commit(commit)
    assert r.incident_label == "decision:break-glass-unaudited"


def test_non_bot_unauthorized_committer_break_glass_still_unauthorized():
    # A non-bot committer NOT in the allowlist with the break-glass subject is
    # still decision:break-glass-unauthorized even with bot_user wired (the
    # short-circuit only matches the bot's own committer login).
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: True,
        clock=lambda: NOW,
        bot_user=BOT,
    )
    r = hook.on_commit(_commit(
        "[break-glass-actions-outage] hotfix",
        author="bystander",             # committer = bystander, not bot
    ))
    assert r.incident_label == "decision:break-glass-unauthorized"
    assert "bystander" in r.incident_body


def test_custom_deadline_respected():
    hook = BreakGlassAuditHook(
        allowlisted_actors=("owner",),
        adr_finder=lambda sha: False,
        adr_deadline_hours=2,  # tighter window
        clock=lambda: NOW,
    )
    ninety_min_ago = (NOW - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Still within 2-hour window.
    r = hook.on_commit(_commit(
        "[break-glass-security] revoke",
        author="owner",
        committed_at=ninety_min_ago,
    ))
    assert r.incident_label is None
    # 3 hours past the 2-hour window.
    three_hours_ago = (NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = hook.on_commit(_commit(
        "[break-glass-security] revoke",
        author="owner",
        committed_at=three_hours_ago,
    ))
    assert r.incident_label == "decision:break-glass-unaudited"
