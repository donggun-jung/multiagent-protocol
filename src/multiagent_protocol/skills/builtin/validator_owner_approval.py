"""C3 — owner approval or classifier auto-approval.

The L1.C3 condition documented in ``docs/concepts/architecture.md``:
a PR may pass only if **either**

  (a) an allowlisted owner reacted 👍 or commented `/approve [A|B|C]` on
      the PR or its linked Decision Inbox issue, **or**
  (b) the classifier returned Quadrant A, B, or C (auto-approval).

Built-in, P0 severity. The actual owner-approval signal sources live in
GitHub (reactions, comments, label events) — for v0.0.x this validator
checks the PR's labels for the resolution labels written by the
``decision_inbox`` module (`decision:approved-A`, `decision:approved-B`,
`decision:approved-C`). Quadrant-based auto-approval is decided by the
classifier engine and surfaced to the bot orchestrator separately;
this validator's job is to enforce **C3** in isolation.

The orchestrator passes the classifier verdict via constructor injection
so a refactor that decouples the classifier from this validator does not
change the validator's pure-function shape.
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

    def __init__(self, classifier_verdict: str | None = None) -> None:
        # ``classifier_verdict`` is the most recently evaluated quadrant for
        # the PR ("A", "B", "C", "D", or None if not yet evaluated). The
        # orchestrator computes this before calling check().
        self.classifier_verdict = classifier_verdict

    def check(self, pr_context: PRContext) -> ValidationResult:
        # Auto-approval path: classifier said A/B/C.
        if self.classifier_verdict in ("A", "B", "C"):
            return ValidationResult.ok()

        # Owner-approval path: the inbox put an approval label on the PR.
        if any(label in pr_context.labels for label in APPROVAL_LABELS):
            return ValidationResult.ok()

        # Otherwise the PR is in Quadrant D and waiting for owner action.
        quadrant_str = self.classifier_verdict or "unknown"
        return ValidationResult.fail(
            f"C3: owner approval missing (quadrant={quadrant_str}). "
            f"Either the classifier needs to vote A/B/C, or an allowlisted "
            f"actor must react 👍 / comment `/approve [A|B|C]` on the linked "
            f"Decision Inbox issue."
        )
