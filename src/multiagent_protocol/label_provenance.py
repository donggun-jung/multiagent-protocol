"""Verified label provenance — shared by every "label downgrades the gate" path.

A persisted label (``decision:approved-*``, ``decision:auto-revert``) is *not*
trusted on presence: that allowed bypasses where a non-allowlisted actor
self-applies the label, or where an approval survives a force-push to new,
unreviewed code. A label is honoured only when it was

  (a) **currently present** on the PR (not since removed),
  (b) **applied by an allowlisted actor or the bot's App user** (via the
      timeline ``labeled`` event — not merely present), and
  (c) **applied at or after the current head commit** — a force-push lands a
      newer commit, so any prior label application is automatically void.

When freshness cannot be established (no head-commit date, or the event has no
timestamp) this fails **closed** (returns False). Both C3 (owner approval) and
the auto-revert classifier use this so the two "label → quadrant downgrade"
doors are guarded identically.
"""

from __future__ import annotations

from collections.abc import Iterable

from multiagent_protocol.types import PRContext


def head_commit_date(pr_context: PRContext) -> str | None:
    """Date of the head commit, or None if it cannot be determined.

    No fallback to other commits: if the head SHA is not among the PR's commits
    the caller must fail closed (a ``max()`` of older dates would hand an
    attacker a too-early baseline that a stale label clears).
    """
    for c in pr_context.commits:
        if c.sha == pr_context.head_sha:
            return c.committed_at  # may be None → caller fails closed
    return None


def has_verified_label(
    pr_context: PRContext,
    labels: Iterable[str],
    allowlisted_actors: tuple[str, ...],
    bot_user: str | None,
) -> bool:
    """True iff one of ``labels`` is present + trusted-applier + at/after head."""
    label_set = set(labels)
    hdate = head_commit_date(pr_context)
    for event in pr_context.label_events:
        if event.label not in label_set:
            continue
        if event.label not in pr_context.labels:
            continue  # label was removed since the event
        actor = event.actor_login
        if actor is None or (actor not in allowlisted_actors and actor != bot_user):
            continue  # self-applied by an untrusted actor
        if not hdate or not event.created_at:
            continue  # freshness unverifiable → fail closed
        if event.created_at < hdate:
            continue  # applied before the current head → force-push voided it
        return True
    return False
