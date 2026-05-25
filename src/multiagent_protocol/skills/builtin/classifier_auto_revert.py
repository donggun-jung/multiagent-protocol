"""Classifier rule: PRs labeled ``decision:auto-revert`` vote Quadrant C.

When the L2 post-merge re-validator detects that a merged commit's required
checks failed (and the failure is not infra-only), it opens a revert PR
labeled ``decision:auto-revert``. Revert PRs are **irreversible** (they
change history-of-record) but **non-critical** in the sense that they
restore a known-good state. C is the right quadrant.

Without this rule, an auto-revert PR would be classified by the path-
default rule, which would likely vote D (because the revert touches the
same critical files the original commit did). D forces owner review on
every auto-revert and slows down the recovery loop.

Built-in ClassifierRule. The L2 module is the only writer of the
``decision:auto-revert`` label; humans should not apply it manually.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    ClassifierVote,
    PRContext,
)

AUTO_REVERT_LABEL = "decision:auto-revert"


class AutoRevertClassifier:
    name = "classifier_auto_revert"

    def evaluate(self, pr_context: PRContext) -> ClassifierVote:
        if AUTO_REVERT_LABEL in pr_context.labels:
            return ClassifierVote(
                quadrant="C",
                reasoning=(
                    "PR carries decision:auto-revert label — bot's own "
                    "L2 follow-up; auto-approve so recovery is not gated."
                ),
            )
        return ClassifierVote(quadrant="A", reasoning="not an auto-revert PR")
