"""C3 — owner approval or classifier auto-approval.

The L1.C3 condition documented in ``docs/concepts/architecture.md``:
a PR may pass only if **either**

  (a) the classifier returned Quadrant A, B, or C (auto-approval), **or**
  (b) the PR carries a ``decision:approved-{A,B}`` label that the bot wrote
      after an allowlisted owner approved it in the Decision Inbox (or that an
      allowlisted owner applied directly).

``decision:approved-C`` is deliberately **not** a merge-granting label: ballot
option C means "defer / needs more info" (``decision_inbox``,
``docs/concepts/four-quadrants.md``) and the inbox records it as
``decision:deferred``. An owner hand-applying ``decision:approved-C`` to note
"I choose C" must not accidentally unlock the merge.

Built-in, P0 severity.

**Why the label alone is not enough.** A persisted ``decision:approved-*``
label is *not* trusted on presence: that allowed two bypasses (a non-allowlisted
collaborator self-applying the label, and an approval surviving a force-push to
new, unreviewed code). C3 therefore re-derives the approval from the timeline:

- **Who applied it** — only an allowlisted actor or the bot's own App user
  (``<bot_app_slug>[bot]``). A self-applied label from anyone else is ignored,
  mirroring C1's actor check on ``ready-to-merge``.
- **Against which head** — an approval label is honoured ONLY through the
  bot's SHA receipt (:mod:`multiagent_protocol.label_provenance`) and only
  while the recorded SHA **equals the current head SHA**, so any new commit —
  even one with a backdated committer timestamp — voids it until re-approved
  via the Decision Inbox. There is NO time-based fallback: a hand-applied
  label without a receipt is not honoured directly; instead the runtime's
  receipt writer converts a fresh allowlisted hand-applied label into a
  head-bound receipt and honours it the SAME tick (the head cannot change
  within a tick's execution, so binding-then-validating against the observed
  head is atomic — there is no one-tick deferral). The residual is a
  force-push in the window before the bot's FIRST observation of an
  out-of-band label; every later force-push is caught by the SHA mismatch,
  and the writer never re-binds an approval (only the Decision Inbox, which
  records the head at question time, may supersede an approval receipt).

The classifier auto-approval path (Quadrant A/B/C) is unconditional and does
not touch labels.
"""

from __future__ import annotations

from collections.abc import Mapping

from multiagent_protocol.label_provenance import has_verified_label
from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
)

APPROVAL_LABELS = (
    "decision:approved-A",
    "decision:approved-B",
    # NOT "decision:approved-C": option C = defer, never a merge grant.
)


class OwnerApprovalValidator:
    name = "validator_owner_approval"
    severity = "P0"

    def __init__(
        self,
        classifier_verdict: str | None = None,
        allowlisted_actors: tuple[str, ...] = (),
        bot_user: str | None = None,
        approved_shas: Mapping[str, str] | None = None,
    ) -> None:
        # ``classifier_verdict``: the PR's quadrant ("A".."D" or None).
        # ``allowlisted_actors``: owner logins permitted to approve.
        # ``bot_user``: the bot App's user login (``<slug>[bot]``) — the bot
        # applies the approval label after verifying an inbox verdict.
        # ``approved_shas``: label → head SHA from the bot's receipt comments
        # (``label_provenance.approval_receipts``); binds each bot-recorded
        # approval to the exact commit it was granted against.
        self.classifier_verdict = classifier_verdict
        self.allowlisted_actors = tuple(allowlisted_actors)
        self.bot_user = bot_user
        self.approved_shas = approved_shas

    def check(self, pr_context: PRContext) -> ValidationResult:
        # Auto-approval path: classifier said A/B/C.
        if self.classifier_verdict in ("A", "B", "C"):
            return ValidationResult.ok()

        # Owner-approval path: a *verified* approval label — applied by the
        # owner/bot and bound to the current head (see label_provenance).
        if has_verified_label(
            pr_context, APPROVAL_LABELS, self.allowlisted_actors, self.bot_user,
            approved_shas=self.approved_shas,
        ):
            return ValidationResult.ok()

        quadrant_str = self.classifier_verdict or "unknown"
        return ValidationResult.fail(
            f"C3: owner approval missing (quadrant={quadrant_str}). "
            f"Either the classifier must vote A/B/C, or an allowlisted actor "
            f"must approve via the Decision Inbox (👍 / `/approve [A|B]`) "
            f"against the current head. A `decision:approved-*` label is "
            f"honoured only via the bot's SHA receipt binding it to the "
            f"current head commit (a fresh hand-applied label is receipted "
            f"by the bot and honoured the same tick)."
        )
