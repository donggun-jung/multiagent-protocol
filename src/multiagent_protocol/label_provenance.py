"""Verified label provenance — shared by every "label downgrades the gate" path.

A persisted label (``decision:approved-*``, ``decision:auto-revert``) is *not*
trusted on presence: that allowed bypasses where a non-allowlisted actor
self-applies the label, or where an approval survives a force-push to new,
unreviewed code. A label is honoured only when it was

  (a) **currently present** on the PR (not since removed),
  (b) **applied by an allowlisted actor or the bot's App user** (via the
      timeline ``labeled`` event — not merely present), and
  (c) **bound to the current head commit**, established one of two ways:

      - **SHA receipt (authoritative).** When the bot applies an approval
        label it also posts a receipt comment on the PR embedding the exact
        head SHA the approval was granted against (see
        :func:`approval_receipt_comment`; the same idea as the inbox issue's
        ``<!-- decision-inbox-head-sha -->`` marker). When a receipt exists
        for a label, the label is honoured **only if** the recorded SHA
        equals the current head SHA — any new commit (including a backdated
        one) voids the approval until re-approved.
      - **Freshness by time (fallback, hand-applied labels only).** With no
        receipt — e.g. the owner applied the label by hand — the label's
        ``labeled`` event must be at or after the head commit's committer
        date. Commit timestamps are attacker-controlled, so this path is
        weaker; as extra safety a head committer date in the future or
        implausibly far in the past is treated as unverifiable.

When freshness cannot be established (no head-commit date, an implausible
head-commit date, or an event with no timestamp) this fails **closed**
(returns False). C3 (owner approval), C1's staleness veto, and the
auto-revert classifier all use this so every "label → gate downgrade" door is
guarded identically.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone

from multiagent_protocol.types import PRContext

# GitHub commit/event timestamps are ISO-8601 UTC ("2026-05-25T00:00:00Z").
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Sanity window for the head commit's committer date (the time-based fallback
# only). Committer dates are attacker-controlled; a date in the future (beyond
# clock skew) or implausibly far in the past is garbage → treat the head date
# as unverifiable, so the time-based path fails closed. The receipt path is
# date-independent and unaffected.
HEAD_DATE_MAX_FUTURE_SKEW = timedelta(minutes=10)
HEAD_DATE_MAX_AGE = timedelta(days=3650)

# Receipt comment markers (HTML comments, like the inbox issue's
# ``<!-- decision-inbox-head-sha -->``). One receipt comment binds one label
# to one head SHA.
APPROVAL_RECEIPT_LABEL_MARKER = "<!-- merge-gate-approval-label:"
APPROVAL_RECEIPT_SHA_MARKER = "<!-- merge-gate-approved-head-sha:"


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


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _DATE_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def plausible_head_date(hdate: str, now: datetime | None = None) -> bool:
    """True iff ``hdate`` parses and is neither in the future nor ancient.

    Unparseable → False (fail closed). ``now`` is injectable for tests;
    defaults to the real clock.
    """
    parsed = _parse_date(hdate)
    if parsed is None:
        return False
    now = now if now is not None else datetime.now(timezone.utc)
    if parsed > now + HEAD_DATE_MAX_FUTURE_SKEW:
        return False
    if parsed < now - HEAD_DATE_MAX_AGE:
        return False
    return True


def approval_receipt_comment(label: str, head_sha: str) -> str:
    """Body of the bot's label-application receipt comment on the PR.

    Posted alongside the label write (``decision_inbox.resolve_open_issues``)
    so the gate can later verify the approval against the exact head SHA it
    was granted for. Parsed back by :func:`approval_receipts`.
    """
    return (
        f"Merge Gate: recorded `{label}` for head `{head_sha[:7]}`.\n\n"
        f"This approval is bound to that exact commit. It becomes void as "
        f"soon as the PR head changes (new commit, amend, or force-push) — "
        f"re-approve via the Decision Inbox against the new head.\n\n"
        f"{APPROVAL_RECEIPT_LABEL_MARKER} {label} -->\n"
        f"{APPROVAL_RECEIPT_SHA_MARKER} {head_sha} -->\n"
    )


def approval_receipts(
    comments: Iterable[dict], bot_user: str | None
) -> dict[str, str]:
    """Parse ``{label: approved_head_sha}`` from the bot's receipt comments.

    Only comments **authored by the bot's own App user** are read — receipts
    are written exclusively by the bot, so a marker in anyone else's comment
    is a forgery attempt and is ignored. The latest receipt per label wins
    (re-approval after a head change supersedes the old binding). With no
    ``bot_user`` no comment can be authenticated → no receipts.
    """
    receipts: dict[str, str] = {}
    if not bot_user:
        return receipts
    for c in comments:
        if ((c.get("user") or {}).get("login")) != bot_user:
            continue
        label = sha = None
        for line in (c.get("body") or "").split("\n"):
            s = line.strip()
            if s.startswith(APPROVAL_RECEIPT_LABEL_MARKER) and s.endswith("-->"):
                label = (
                    s.removeprefix(APPROVAL_RECEIPT_LABEL_MARKER)
                    .removesuffix("-->").strip()
                )
            elif s.startswith(APPROVAL_RECEIPT_SHA_MARKER) and s.endswith("-->"):
                sha = (
                    s.removeprefix(APPROVAL_RECEIPT_SHA_MARKER)
                    .removesuffix("-->").strip()
                )
        if label and sha:
            receipts[label] = sha
    return receipts


def has_verified_label(
    pr_context: PRContext,
    labels: Iterable[str],
    allowlisted_actors: tuple[str, ...],
    bot_user: str | None,
    *,
    approved_shas: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> bool:
    """True iff one of ``labels`` is present + trusted-applier + head-bound.

    ``approved_shas`` maps label name → the head SHA the bot recorded the
    approval against (from :func:`approval_receipts`). A label with a receipt
    is honoured **only** when the recorded SHA equals the current head SHA —
    the time-based fallback never applies to it (a backdated commit must not
    resurrect a stale approval). A label without a receipt falls back to the
    at-or-after-head time check, hardened by :func:`plausible_head_date`.
    """
    label_set = set(labels)
    hdate = head_commit_date(pr_context)
    if hdate is not None and not plausible_head_date(hdate, now):
        hdate = None  # implausible committer date → time path fails closed
    for event in pr_context.label_events:
        if event.label not in label_set:
            continue
        if event.label not in pr_context.labels:
            continue  # label was removed since the event
        actor = event.actor_login
        if actor is None or (actor not in allowlisted_actors and actor != bot_user):
            continue  # self-applied by an untrusted actor
        bound = None if approved_shas is None else approved_shas.get(event.label)
        if bound is not None:
            if bound == pr_context.head_sha:
                return True
            continue  # recorded against a different head → void until re-approved
        if not hdate or not event.created_at:
            continue  # freshness unverifiable → fail closed
        if event.created_at < hdate:
            continue  # applied before the current head → force-push voided it
        return True
    return False
