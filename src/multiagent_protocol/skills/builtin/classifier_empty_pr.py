"""Empty PRs → Quadrant D.

A PR with zero file changes is suspicious — either a bot bug, a race
condition, or a probe. Forcing owner review is cheap defense. See
``docs/concepts/general-preferences.md`` § "4. Empty PR is Quadrant D".
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    ClassifierRule,
    ClassifierVote,
    PRContext,
)


class EmptyPrClassifier:
    name = "classifier_empty_pr"

    def evaluate(self, pr_context: PRContext) -> ClassifierVote:
        if pr_context.file_count == 0:
            return ClassifierVote(
                quadrant="D",
                reasoning="empty PR (zero file changes) — owner approval required",
            )
        return ClassifierVote(quadrant="A", reasoning="not empty")
