"""C1 — ``ready-to-merge`` label present, applied by an allowlisted actor.

Built-in, P0 severity. The label is the explicit signal that the PR author
(or designated reviewer) considers the PR ready. Without this, no merge.

The "applied by allowlisted actor" check uses the LabelEvent history attached
to the PR; the bot reads only label-add events authored by users in
``config.owner.allowlisted_actors``.

**SHA receipt REQUIRED (mirrors C3 / owner approval).** ``ready-to-merge`` is
a gate-opening label, so — exactly like ``decision:approved-*`` — it is
honoured ONLY through the bot's SHA receipt
(:mod:`multiagent_protocol.label_provenance`) bound to the **current** head
commit. The runtime's receipt writer records that receipt the first time it
sees an allowlisted-applied ready label (and the runtime honours it the SAME
tick, since the head cannot change within a tick's execution). C1 therefore
passes only when a bot receipt for the label exists whose SHA equals the
current head:

- **no receipt → C1 fails** (the label alone, even from an allowlisted actor,
  never opens the gate; the bot records a receipt this tick and C1 passes once
  one exists at the current head);
- **stale receipt (recorded SHA != current head) → C1 fails** until the label
  is re-bound against the new head (the owner re-applies ``ready-to-merge``;
  the bot re-binds on proof of that fresh intent).

Commit timestamps play no part anywhere: a backdated commit can neither keep a
stale ready label alive nor satisfy C1. The allowlisted-actor check below is
retained as defense-in-depth on top of the receipt requirement.
"""

from __future__ import annotations

from collections.abc import Mapping

from multiagent_protocol.label_provenance import effective_label_applier
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
        # Loader injects ``allowlisted_actors`` via configuration; default
        # empty means "any actor may have applied the label" (the receipt
        # requirement still applies — the actor check is only relaxed).
        # ``approved_shas`` (label → head SHA from the bot's receipt comments)
        # is injected per-PR by the runtime. The label is REQUIRED to carry a
        # bot receipt bound to the current head (mirrors C3); ``None`` is
        # treated like an empty map → no receipt.
        self.allowlisted_actors = allowlisted_actors or ()
        self.approved_shas = approved_shas

    def check(self, pr_context: PRContext) -> ValidationResult:
        if READY_LABEL not in pr_context.labels:
            return ValidationResult.fail(
                f"C1: label '{READY_LABEL}' not set on PR "
                f"#{pr_context.number}"
            )
        # Receipt REQUIRED (mirrors C3): the label opens C1 only with a bot
        # SHA receipt bound to the CURRENT head. None map == no receipt.
        bound = None if self.approved_shas is None else self.approved_shas.get(READY_LABEL)
        if bound is None:
            return ValidationResult.fail(
                f"C1: label '{READY_LABEL}' has no current-head receipt — the "
                f"label alone does not open the gate; honoured once the bot "
                f"records a receipt at the current head "
                f"({pr_context.head_sha[:7]})."
            )
        if bound != pr_context.head_sha:
            return ValidationResult.fail(
                f"C1: label '{READY_LABEL}' was recorded for head "
                f"{bound[:7]} but the PR head is now "
                f"{pr_context.head_sha[:7]} — stale; re-apply against the "
                f"current head."
            )
        # Receipt matches the current head. The allowlisted-actor check below
        # is defense-in-depth (mirrors C3's applier check on top of the SHA
        # receipt); an empty allowlist relaxes only this actor check.
        if not self.allowlisted_actors:
            return ValidationResult.ok()
        # The CURRENT presence of the label must have been established by an
        # allowlisted actor — the most recent add after the most recent removal,
        # NOT any historical trusted add. A trusted-add → unlabeled → untrusted
        # re-add (even at an unchanged head, so the receipt still SHA-matches)
        # is therefore rejected here, mirroring C3/auto-revert provenance.
        applier = effective_label_applier(
            READY_LABEL, pr_context.label_events, pr_context.unlabel_events
        )
        if applier in self.allowlisted_actors:
            return ValidationResult.ok()
        return ValidationResult.fail(
            f"C1: label '{READY_LABEL}' was not applied by an "
            f"allowlisted actor (allowlist: "
            f"{', '.join(self.allowlisted_actors)})"
        )
