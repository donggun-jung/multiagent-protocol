"""Verified label provenance — shared by every "label downgrades the gate" path.

A persisted label (``decision:approved-*``, ``decision:auto-revert``) is *not*
trusted on presence: that allowed bypasses where a non-allowlisted actor
self-applies the label, or where an approval survives a force-push to new,
unreviewed code. A label is honoured only when it was

  (a) **currently present** on the PR (not since removed),
  (b) **applied by an allowlisted actor or the bot's App user** (via the
      timeline ``labeled`` event — not merely present), and
  (c) **bound to the current head commit**:

      - **SHA receipt (REQUIRED for the gate-opening labels).** The bot posts
        a receipt comment on the PR embedding the exact head SHA a label was
        recorded against (see :func:`approval_receipt_comment`; the same idea
        as the inbox issue's ``<!-- decision-inbox-head-sha -->`` marker).
        The labels in :data:`RECEIPT_ELIGIBLE_LABELS` (``decision:approved-A``,
        ``decision:approved-B``, ``ready-to-merge``) are honoured **only**
        while a receipt exists whose recorded SHA equals the current head SHA.
        No receipt → not honoured; any new commit — including one with a
        backdated committer timestamp — voids the binding. There is NO
        time-based fallback for these labels: committer dates are
        client-supplied and a backdated head must never resurrect a stale
        approval. Receipts are written by the Decision Inbox flow
        (``decision_inbox.resolve_open_issues``) and by the runtime's receipt
        writer, which converts an allowlisted hand-applied label into a
        head-bound receipt (:func:`labels_needing_receipt`).

        **Irreducible residual (out-of-band labels).** When the bot writes the
        FIRST receipt for a hand-applied label it can only bind to the head it
        observes at that moment — for a label applied out of band the bot has
        no non-forgeable signal for which head the authorizer actually saw. So
        a force-push in the window between the authorizer applying the label
        and the bot's first observation of it is the residual; every later
        force-push is caught because the receipt's SHA then differs from the
        current head. The only fully airtight channel is the Decision Inbox,
        which records the head at question time (the ``decision-inbox-head-sha``
        marker) and tamper-guards the vote — the bot never auto-re-binds an
        approval; superseding one is the inbox's job alone.
      - **Freshness by time (non-receipt labels only).** A label outside the
        receipt-eligible set (today: ``decision:auto-revert``) falls back to
        requiring its ``labeled`` event at or after the head commit's
        committer date. Commit timestamps are attacker-controlled, so this
        path is weaker; a head committer date in the future, implausibly far
        in the past, or unparseable is treated as unverifiable.

When freshness cannot be established (no head-commit date, an implausible or
unparseable head-commit date, an event with a missing/garbage timestamp, or a
missing receipt for a receipt-eligible label) this fails **closed** (returns
False). C3 (owner approval), C1's staleness veto, and the auto-revert
classifier all use this so every "label → gate downgrade" door is guarded
identically.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone

from multiagent_protocol.types import LabelEvent, PRContext

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

# The gate-opening labels that are honoured ONLY through a bot SHA receipt
# bound to the current head (no time-based fallback). Mirrors
# ``validator_owner_approval.APPROVAL_LABELS`` + ``validator_ready_to_merge.
# READY_LABEL`` (literals duplicated to keep this module dependency-free; a
# doctrine test pins them equal).
RECEIPT_ELIGIBLE_LABELS = frozenset({
    "decision:approved-A",
    "decision:approved-B",
    "ready-to-merge",
})

# Of the receipt-eligible labels, the only one the runtime may RE-bind to a
# new head after its receipt went stale — and only on proof of fresh owner
# intent (a trusted ``labeled`` event NEWER than the bot's latest receipt
# comment; both timestamps are server-assigned, unlike committer dates).
# ``decision:approved-*`` receipts are deliberately NOT re-bindable here:
# superseding a stale approval is the Decision Inbox's job, where the owner
# re-approves against a verified head. Auto-re-binding an approval would be
# auto-re-approval of unreviewed code.
REBINDABLE_LABELS = frozenset({"ready-to-merge"})


def establishing_labeled_event(
    label: str,
    label_events: Iterable[LabelEvent],
    unlabel_events: Iterable[LabelEvent],
) -> LabelEvent | None:
    """The ``labeled`` event that established a label's CURRENT presence.

    A label may be added by a trusted actor, removed, then re-added by an
    UNtrusted actor: the stale earlier trusted ``labeled`` event must not keep
    authenticating it. The *establishing* add is the most recent ``labeled``
    event for ``label`` whose GitHub-assigned timestamp is at-or-after the most
    recent ``unlabeled`` event for the same label (the add that re-created its
    current presence). Returns None when none can be determined — every caller
    treats that as **fail closed** (untrusted / not honoured).

    - No ``unlabeled`` event for the label → never removed, so the latest
      ``labeled`` (timeline order) establishes it. On a real GitHub timeline a
      label name has at most one ``labeled`` with no intervening ``unlabeled``,
      so this preserves the prior single-event behaviour exactly.
    - Removed at least once → only a ``labeled`` at-or-after the latest removal
      can be the establisher; the most recent such add (by event timestamp)
      wins. A ``labeled`` whose timestamp is unparseable or strictly before the
      latest removal cannot be proven to establish the present label and is
      ignored (fail closed) — so a stale pre-removal trusted add is never the
      effective applier.

    GitHub event timestamps only are consulted here (never commit dates).
    """
    labeleds = [e for e in label_events if e.label == label]
    if not labeleds:
        return None
    last_unlabel_dt: datetime | None = None
    for e in unlabel_events:
        if e.label != label:
            continue
        dt = _parse_date(e.created_at)
        if dt is not None and (last_unlabel_dt is None or dt > last_unlabel_dt):
            last_unlabel_dt = dt
    if last_unlabel_dt is None:
        # Never removed: the latest add in chronological timeline order.
        return labeleds[-1]
    establisher: LabelEvent | None = None
    establisher_dt: datetime | None = None
    for e in labeleds:
        dt = _parse_date(e.created_at)
        if dt is None or dt < last_unlabel_dt:
            continue  # cannot prove this add re-established the current presence
        # ">=" so the most recently RECORDED add at the max timestamp wins
        # (timeline order breaks a same-second tie toward the latest event).
        if establisher_dt is None or dt >= establisher_dt:
            establisher, establisher_dt = e, dt
    return establisher


def effective_label_applier(
    label: str,
    label_events: Iterable[LabelEvent],
    unlabel_events: Iterable[LabelEvent],
) -> str | None:
    """Login of the actor whose add established the label's current presence.

    Thin wrapper over :func:`establishing_labeled_event` returning just the
    actor login (None when no establishing add can be determined). C1's
    defense-in-depth actor check uses this so a remove-then-untrusted-readd is
    rejected exactly like C3/auto-revert.
    """
    event = establishing_labeled_event(label, label_events, unlabel_events)
    return event.actor_login if event is not None else None


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


def _parse_date(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, else None.

    GitHub emits ``2026-05-25T00:00:00Z``; a trailing ``Z`` (or a numeric
    UTC offset, or fractional seconds) is handled, and a naive timestamp is
    taken as UTC. Anything unparseable returns None — every caller treats
    that as **fail closed** (not fresh / not plausible / event ignored), so
    garbage in a freshness-relevant field can never widen the gate.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _iter_receipt_comments(comments: Iterable[dict], bot_user: str | None):
    """Yield ``(label, sha, comment_created_at)`` per bot-authored receipt.

    Only comments **authored by the bot's own App user** are read — receipts
    are written exclusively by the bot, so a marker in anyone else's comment
    is a forgery attempt and is ignored. With no ``bot_user`` no comment can
    be authenticated → nothing is yielded.
    """
    if not bot_user:
        return
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
            yield label, sha, c.get("created_at")


def approval_receipts(
    comments: Iterable[dict], bot_user: str | None
) -> dict[str, str]:
    """Parse ``{label: approved_head_sha}`` from the bot's receipt comments.

    The latest receipt per label wins (re-approval after a head change
    supersedes the old binding). See :func:`_iter_receipt_comments` for the
    author-authentication rules.
    """
    return {label: sha for label, sha, _ in _iter_receipt_comments(comments, bot_user)}


def approval_receipt_times(
    comments: Iterable[dict], bot_user: str | None
) -> dict[str, str]:
    """``{label: created_at}`` of the bot's LATEST receipt comment per label.

    The comment's ``created_at`` is **server-assigned** by GitHub (unlike a
    commit's committer date, it cannot be forged by a PR author). It is used
    by :func:`labels_needing_receipt` to decide whether the owner re-applied
    a label AFTER the bot's last binding — the proof of fresh intent a
    re-bind requires. A receipt comment without a usable timestamp simply
    yields no entry (the re-bind then fails closed).
    """
    times: dict[str, str] = {}
    for label, _sha, created_at in _iter_receipt_comments(comments, bot_user):
        times[label] = created_at if isinstance(created_at, str) else ""
    return times


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
    label against (from :func:`approval_receipts`). A label with a receipt is
    honoured **only** when the recorded SHA equals the current head SHA.

    A label in :data:`RECEIPT_ELIGIBLE_LABELS` is NEVER honoured without a
    receipt — there is no time-based fallback for the gate-opening labels (a
    backdated commit must not resurrect a stale approval; the runtime's
    receipt writer converts a fresh allowlisted hand-applied label into a
    receipt, honoured from the next tick). Only a non-receipt label (today:
    ``decision:auto-revert``) falls back to the at-or-after-head time check,
    hardened by :func:`plausible_head_date` and strict timestamp parsing.
    """
    label_set = set(labels)
    hdate = head_commit_date(pr_context)
    head_dt = _parse_date(hdate)
    if head_dt is not None and not plausible_head_date(hdate, now):
        head_dt = None  # implausible committer date → time path fails closed
    for label in label_set:
        if label not in pr_context.labels:
            continue  # label was removed since (not currently present)
        # The trusted-applier check uses the add that ESTABLISHED the current
        # presence (most recent labeled after the most recent unlabeled), not
        # any historical trusted add: a trusted-add → unlabeled → untrusted
        # re-add must NOT stay authenticated by the stale earlier trusted event.
        event = establishing_labeled_event(
            label, pr_context.label_events, pr_context.unlabel_events
        )
        if event is None:
            continue  # no determinable establishing add → fail closed
        actor = event.actor_login
        if actor is None or (actor not in allowlisted_actors and actor != bot_user):
            continue  # current presence established by an untrusted actor
        bound = None if approved_shas is None else approved_shas.get(label)
        if bound is not None:
            if bound == pr_context.head_sha:
                return True
            continue  # recorded against a different head → void until re-approved
        if label in RECEIPT_ELIGIBLE_LABELS:
            continue  # gate-opening label without a receipt → never honoured
        event_dt = _parse_date(event.created_at)
        if head_dt is None or event_dt is None:
            continue  # freshness unverifiable (absent/garbage timestamp) → fail closed
        if event_dt < head_dt:
            continue  # applied before the current head → force-push voided it
        return True
    return False


def labels_needing_receipt(
    pr_context: PRContext,
    allowlisted_actors: tuple[str, ...],
    bot_user: str | None,
    *,
    approved_shas: Mapping[str, str],
    receipt_times: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Receipt-eligible labels the bot should bind to the CURRENT head now.

    Receipt-eligible labels are honoured only through a bot SHA receipt
    (:func:`has_verified_label`), so a label the owner applied BY HAND would
    otherwise be dead. This decides which hand-applied labels the runtime
    converts into receipts; the runtime posts :func:`approval_receipt_comment`
    for each and binds it to the head observed THIS tick.

    A label qualifies only when ALL hold (each check fails closed):

    - it is **currently present**, with a timeline ``labeled`` event by an
      allowlisted actor or the bot (the same trusted-applier rule as
      :func:`has_verified_label`);
    - it has **no receipt yet** (first bind), or — for
      :data:`REBINDABLE_LABELS` only — its receipt is stale (older head) AND
      the trusted label event is strictly NEWER than the bot's latest receipt
      comment for it (server-assigned timestamps on both sides), i.e. the
      owner re-applied the label after the last binding. Approval labels are
      never re-bound here: a stale approval is superseded only by a fresh
      Decision Inbox approval against a verified head.

    **No committer date is consulted.** A commit's committer date is
    client-supplied and forgeable, so it can never be a freshness signal: the
    old "label event at-or-after the head commit date" gate was security
    theatre (an attacker backdates the force-pushed head and clears it). For a
    label applied OUT OF BAND the bot cannot know which head the authorizer
    saw, so the first receipt binds to the head the bot observes here. The
    irreducible residual is therefore a force-push in the window between the
    authorizer applying the label and the bot's FIRST observation of it; every
    LATER force-push is caught by the receipt-vs-current-head re-check in
    :func:`has_verified_label` (the bot refuses to re-bind an approval — only
    the airtight Decision Inbox, which records the head at question time, may
    supersede an approval receipt). The re-bind freshness check above relies
    solely on GitHub-assigned timestamps (the receipt comment's ``created_at``
    and the label event's ``created_at``), never a committer date.
    """
    receipt_times = receipt_times or {}
    out: list[str] = []
    for label in sorted(RECEIPT_ELIGIBLE_LABELS):
        if label not in pr_context.labels:
            continue
        bound = approved_shas.get(label)
        if bound == pr_context.head_sha:
            continue  # already bound to the current head
        min_event_dt = None
        if bound is not None:  # a stale receipt exists
            if label not in REBINDABLE_LABELS:
                continue  # approvals: only the inbox may supersede a receipt
            min_event_dt = _parse_date(receipt_times.get(label))
            if min_event_dt is None:
                continue  # cannot prove a re-apply postdates the binding
        # The applier that matters is the one who ESTABLISHED the current
        # presence (most recent labeled after the most recent unlabeled): a
        # trusted-add → unlabeled → untrusted re-add must get NO receipt, so a
        # stale earlier trusted add can never mint one for an untrusted re-add.
        event = establishing_labeled_event(
            label, pr_context.label_events, pr_context.unlabel_events
        )
        if event is None:
            continue
        actor = event.actor_login
        if actor is None or (actor not in allowlisted_actors and actor != bot_user):
            continue
        if min_event_dt is not None:
            # Re-bind needs proof of fresh owner intent: the establishing label
            # event strictly NEWER than the bot's last receipt comment (both
            # timestamps GitHub-assigned, not forgeable).
            event_dt = _parse_date(event.created_at)
            if event_dt is None or event_dt <= min_event_dt:
                continue
        out.append(label)
    return tuple(out)
