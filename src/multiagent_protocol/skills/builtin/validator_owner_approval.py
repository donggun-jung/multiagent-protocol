"""C3 — owner approval or classifier auto-approval.

The L1.C3 condition documented in ``docs/concepts/architecture.md``:
a PR may pass only if **either**

  (a) the classifier returned Quadrant A, B, or C (auto-approval), **or**
  (b) the PR carries a ``decision:approved-{A,B,C}`` label that the bot wrote
      after an allowlisted owner approved it in the Decision Inbox (or that an
      allowlisted owner applied directly).

Built-in, P0 severity.

**Why the label alone is not enough.** A persisted ``decision:approved-*``
label is *not* trusted on presence: that allowed two bypasses (a non-allowlisted
collaborator self-applying the label, and an approval surviving a force-push to
new, unreviewed code). C3 therefore re-derives the approval from the timeline:

- **Who applied it** — only an allowlisted actor or the bot's own App user
  (``<bot_app_slug>[bot]``). A self-applied label from anyone else is ignored,
  mirroring C1's actor check on ``ready-to-merge``.
- **Against which head** — the approval's ``labeled`` event must be **at or
  after** the current head commit. A force-push lands a newer commit, so any
  prior approval is automatically voided and the PR returns to the inbox.

The classifier auto-approval path (Quadrant A/B/C) is unconditional and does
not touch labels.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
)

APPROVAL_LABELS = (
    "decision:approved-A",
    "decision:approved-B",
    "decision:approved-C",
)


class OwnerApprovalValidator:
    name = "validator_owner_approval"
    severity = "P0"

    def __init__(
        self,
        classifier_verdict: str | None = None,
        allowlisted_actors: tuple[str, ...] = (),
        bot_user: str | None = None,
    ) -> None:
        # ``classifier_verdict``: the PR's quadrant ("A".."D" or None).
        # ``allowlisted_actors``: owner logins permitted to approve.
        # ``bot_user``: the bot App's user login (``<slug>[bot]``) — the bot
        # applies the approval label after verifying an inbox verdict.
        self.classifier_verdict = classifier_verdict
        self.allowlisted_actors = tuple(allowlisted_actors)
        self.bot_user = bot_user

    def check(self, pr_context: PRContext) -> ValidationResult:
        # Auto-approval path: classifier said A/B/C.
        if self.classifier_verdict in ("A", "B", "C"):
            return ValidationResult.ok()

        # Owner-approval path: a *verified* approval label.
        if self._has_verified_approval(pr_context):
            return ValidationResult.ok()

        quadrant_str = self.classifier_verdict or "unknown"
        return ValidationResult.fail(
            f"C3: owner approval missing (quadrant={quadrant_str}). "
            f"Either the classifier must vote A/B/C, or an allowlisted actor "
            f"must approve via the Decision Inbox (👍 / `/approve [A|B|C]`) "
            f"against the current head. A bare `decision:approved-*` label is "
            f"not honoured unless it was applied by the owner/bot at or after "
            f"the current head commit."
        )

    def _trusted_applier(self, actor: str | None) -> bool:
        return actor is not None and (
            actor in self.allowlisted_actors or actor == self.bot_user
        )

    def _head_commit_date(self, pr_context: PRContext) -> str | None:
        """Date of the **head** commit, or None if it cannot be determined.

        No fallback to other commits: if the head SHA is not among the PR's
        commits, or carries no date, freshness is unverifiable and the caller
        must fail **closed** (a ``max()`` of older commit dates would hand an
        attacker a too-early baseline that a stale approval clears)."""
        for c in pr_context.commits:
            if c.sha == pr_context.head_sha:
                return c.committed_at  # may be None → caller fails closed
        return None

    def _has_verified_approval(self, pr_context: PRContext) -> bool:
        head_date = self._head_commit_date(pr_context)
        for event in pr_context.label_events:
            if event.label not in APPROVAL_LABELS:
                continue
            # The label must still be present (not since removed).
            if event.label not in pr_context.labels:
                continue
            # Applied by an allowlisted owner or the bot — not self-applied.
            if not self._trusted_applier(event.actor_login):
                continue
            # Freshness must be *establishable*: without a head-commit date or
            # an event timestamp we cannot prove the approval post-dates the
            # head, so we fail closed (a force-push must void prior approval).
            if not head_date or not event.created_at:
                continue
            if event.created_at < head_date:
                continue
            return True
        return False
