"""C1 — ``ready-to-merge`` label present, applied by an allowlisted actor.

Built-in, P0 severity. The label is the explicit signal that the PR author
(or designated reviewer) considers the PR ready. Without this, no merge.

The "applied by allowlisted actor" check uses the LabelEvent history attached
to the PR; the bot reads only label-add events authored by users in
``config.owner.allowlisted_actors``.

**SHA staleness veto.** When the bot recorded the ready label it also posted
a SHA receipt (:mod:`multiagent_protocol.label_provenance`) binding it to the
exact head commit. If such a receipt exists and the PR head has since moved,
the label is stale and C1 fails until the label is re-recorded against the
new head — commit timestamps play no part, so a backdated commit cannot keep
a stale ready label alive.
"""

from __future__ import annotations

from collections.abc import Mapping

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
)

READY_LABEL = "ready-to-merge"


class ReadyToMergeValidator:
    name = "validator_ready_to_merge"
    severity = "P0"

    def __init__(self, allowlisted_actors: tuple[str, ...] | None = None,
                 approved_shas: Mapping[str, str] | None = None) -> None:
        # Loader injects this via configuration; default empty means
        # "any actor can apply the label" (relaxed, suitable for testing).
        # ``approved_shas`` (label → head SHA from the bot's receipt comments)
        # is injected per-PR by the runtime; used only as a staleness VETO —
        # it never grants C1 by itself.
        self.allowlisted_actors = allowlisted_actors or ()
        self.approved_shas = approved_shas

    def check(self, pr_context: PRContext) -> ValidationResult:
        if READY_LABEL not in pr_context.labels:
            return ValidationResult.fail(
                f"C1: label '{READY_LABEL}' not set on PR "
                f"#{pr_context.number}"
            )
        # Staleness veto: a bot-recorded ready label is bound to the exact
        # head SHA in its receipt; any newer head voids it (fail closed).
        bound = None if self.approved_shas is None else self.approved_shas.get(READY_LABEL)
        if bound is not None and bound != pr_context.head_sha:
            return ValidationResult.fail(
                f"C1: label '{READY_LABEL}' was recorded for head "
                f"{bound[:7]} but the PR head is now "
                f"{pr_context.head_sha[:7]} — stale; re-apply against the "
                f"current head."
            )
        if not self.allowlisted_actors:
            return ValidationResult.ok()
        # The label must have been applied by an allowlisted actor at some
        # point in PR history. We accept the most recent add event by such
        # an actor.
        for event in reversed(pr_context.label_events):
            if event.label == READY_LABEL and event.actor_login in self.allowlisted_actors:
                return ValidationResult.ok()
        return ValidationResult.fail(
            f"C1: label '{READY_LABEL}' was not applied by an "
            f"allowlisted actor (allowlist: "
            f"{', '.join(self.allowlisted_actors)})"
        )
