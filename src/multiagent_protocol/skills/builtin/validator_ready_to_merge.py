"""C1 — ``ready-to-merge`` label present, applied by an allowlisted actor.

Built-in, P0 severity. The label is the explicit signal that the PR author
(or designated reviewer) considers the PR ready. Without this, no merge.

The "applied by allowlisted actor" check uses the LabelEvent history attached
to the PR; the bot reads only label-add events authored by users in
``config.owner.allowlisted_actors``.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
    Validator,
)

READY_LABEL = "ready-to-merge"


class ReadyToMergeValidator:
    name = "validator_ready_to_merge"
    severity = "P0"

    def __init__(self, allowlisted_actors: tuple[str, ...] | None = None) -> None:
        # Loader injects this via configuration; default empty means
        # "any actor can apply the label" (relaxed, suitable for testing).
        self.allowlisted_actors = allowlisted_actors or ()

    def check(self, pr_context: PRContext) -> ValidationResult:
        if READY_LABEL not in pr_context.labels:
            return ValidationResult.fail(
                f"C1: label '{READY_LABEL}' not set on PR "
                f"#{pr_context.number}"
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
