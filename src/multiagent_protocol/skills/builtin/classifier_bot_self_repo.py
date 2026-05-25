"""PRs targeting the bot's own repository → Quadrant D.

The bot does not gate its own PRs (chicken-and-egg). Quadrant D ensures
owner sees them. The only paths to merge are: ``[break-glass-bot-self-update]``
direct push + ADR-within-24h, or owner-approved manual merge.

See ``docs/concepts/general-preferences.md`` § "5. Bot's own repo PRs are
Quadrant D" and ``docs/concepts/break-glass.md``.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    ClassifierRule,
    ClassifierVote,
    PRContext,
)


class BotSelfRepoClassifier:
    name = "classifier_bot_self_repo"

    def __init__(self, bot_repo_full_name: str | None = None) -> None:
        # Caller supplies the bot's own repo identifier via config; default
        # ``None`` means "this rule has no opinion" (always vote A). Tests
        # construct with the actual repo to exercise the D path.
        self.bot_repo_full_name = bot_repo_full_name

    def evaluate(self, pr_context: PRContext) -> ClassifierVote:
        if (
            self.bot_repo_full_name is not None
            and pr_context.full_name == self.bot_repo_full_name
        ):
            return ClassifierVote(
                quadrant="D",
                reasoning=(
                    f"PR targets the bot's own repo ({self.bot_repo_full_name}); "
                    f"owner approval required — bot does not gate its own PRs."
                ),
            )
        return ClassifierVote(quadrant="A", reasoning="not bot repo")
