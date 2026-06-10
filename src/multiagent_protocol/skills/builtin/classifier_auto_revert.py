"""Classifier rule: a *verified* ``decision:auto-revert`` label votes Quadrant C.

A revert PR restores a known-good state, so it is recorded as recovery rather
than a generic change. The classifier takes the **maximum** quadrant
(D > B > C > A), so this vote **cannot pull a genuinely Quadrant-D revert**
(e.g. one that deletes a critical file) down to C — such a revert still routes
to the owner, the safe default. Its effect is on reverts the other rules rate
A/B/C (all of which already auto-merge): it labels them as recovery.

**Security (defense-in-depth on a label door).** Even though max-vote already
prevents a label from lowering a Quadrant-D verdict, this label is honoured
only when it passes the same provenance check as a ``decision:approved-*``
label (:mod:`multiagent_protocol.label_provenance`): applied by an allowlisted
actor or the bot's App user, **bound to the current head commit** — via the
bot's SHA receipt when one exists (exact head SHA match), else the
at-or-after-head time check. A label self-applied by an untrusted
collaborator — or surviving a force-push — is ignored. This keeps every
"label → quadrant" door guarded identically to C3.

Until automatic L2 revert-PR creation ships (post-1.0; see ``STATUS.md``), the
only legitimate applier is the **owner**, fast-tracking a revert PR they
opened in response to a ``decision:post-merge-revalidation`` incident. When the
bot authors revert PRs itself, the bot's App user becomes the applier.
"""

from __future__ import annotations

from collections.abc import Mapping

from multiagent_protocol.label_provenance import has_verified_label
from multiagent_protocol.skills.base import (
    ClassifierVote,
    PRContext,
)

AUTO_REVERT_LABEL = "decision:auto-revert"


class AutoRevertClassifier:
    name = "classifier_auto_revert"

    def __init__(
        self,
        allowlisted_actors: tuple[str, ...] = (),
        bot_user: str | None = None,
        approved_shas: Mapping[str, str] | None = None,
    ) -> None:
        # Injected by the orchestrator. With the defaults (empty allowlist, no
        # bot user) no label is ever trusted, so the loader's 0-arg instance is
        # a safe no-op; the configured instance does the real check.
        # ``approved_shas`` (label → head SHA from the bot's receipt comments)
        # is injected per-PR by the runtime: a bot-recorded label is honoured
        # only at the exact head it was recorded against.
        self.allowlisted_actors = tuple(allowlisted_actors)
        self.bot_user = bot_user
        self.approved_shas = approved_shas

    def evaluate(self, pr_context: PRContext) -> ClassifierVote:
        if has_verified_label(
            pr_context, (AUTO_REVERT_LABEL,), self.allowlisted_actors, self.bot_user,
            approved_shas=self.approved_shas,
        ):
            return ClassifierVote(
                quadrant="C",
                reasoning=(
                    "verified decision:auto-revert label (owner/bot, bound to "
                    "the current head) — fast-track recovery, not owner-gated."
                ),
            )
        # Absent, self-applied by an untrusted actor, or stale → no opinion;
        # the PR's real quadrant (from the other rules) decides.
        return ClassifierVote(
            quadrant="A", reasoning="no verified auto-revert label"
        )
