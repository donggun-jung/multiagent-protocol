"""Plugin Protocol interfaces for the skills system.

Three interfaces:

- :class:`Validator` — pass/fail check that contributes to L1.
- :class:`ClassifierRule` — votes a Quadrant (A/B/C/D) for the classifier.
- :class:`BranchHook` — runs once per commit on ``main``, returns an incident
  or :func:`BranchHookResult.none`.

See ``docs/concepts/skills-plugin.md`` for the full specification, including
the loader, security model, and severity tiers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from multiagent_protocol.types import (
    BranchHookResult,
    ClassifierVote,
    CommitContext,
    PRContext,
    Severity,
    ValidationResult,
)


@runtime_checkable
class Validator(Protocol):
    """A pass/fail check that contributes to L1.

    All registered Validators run on each PR each cron tick. A failing
    Validator at ``severity = "P0"`` blocks the L1 gate; lower severities
    warn or audit only. See ``docs/concepts/skills-plugin.md`` §
    "Severity override table" and ``docs/concepts/general-preferences.md``.
    """

    name: str
    severity: Severity

    def check(self, pr_context: PRContext) -> ValidationResult: ...


@runtime_checkable
class ClassifierRule(Protocol):
    """A rule that votes one Quadrant for the 4-quadrant classifier.

    All registered rules vote; the final verdict is the **maximum** quadrant
    across all votes (D > B > C > A). A user-added rule cannot **lower** the
    verdict — voting A from a user rule is silently ignored if any other rule
    voted higher.
    """

    name: str

    def evaluate(self, pr_context: PRContext) -> ClassifierVote: ...


@runtime_checkable
class BranchHook(Protocol):
    """A hook that runs once per commit on ``main``, after L2 + L5 watermark.

    Returns :func:`BranchHookResult.none` for "no incident" or a populated
    :class:`BranchHookResult` to open an incident issue.
    """

    name: str

    def on_commit(self, commit: CommitContext) -> BranchHookResult: ...


# Convenience exports so skills only need ``from multiagent_protocol.skills.base import ...``.
__all__ = [
    "Validator",
    "ClassifierRule",
    "BranchHook",
    "ValidationResult",
    "ClassifierVote",
    "BranchHookResult",
    "PRContext",
    "CommitContext",
    "Severity",
]
