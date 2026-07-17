"""C5 — every commit in the PR has well-formed ``Agent-*`` + ``Task-Ref`` trailers.

Built-in, P0 severity. Cannot be disabled via ``config.skills.disabled``;
only severity can be overridden. See ``docs/concepts/general-preferences.md``
§ "3. Agent-* commit trailers required".
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
)
from multiagent_protocol.trailer_contract import (
    AGENT_SESSION_PATTERN,
    TASK_REF_PATTERN,
)

# Compatibility alias for callers that imported the former module-local
# constant.  The executable definition now lives in ``trailer_contract``.
SESSION_PATTERN = AGENT_SESSION_PATTERN


class TrailersValidator:
    name = "validator_trailers"
    severity = "P0"

    REQUIRED_TRAILERS = ("agent_tool", "agent_model", "agent_session",
                         "agent_machine", "task_ref")

    def check(self, pr_context: PRContext) -> ValidationResult:
        for commit in pr_context.commits:
            t = commit.trailers
            for key in self.REQUIRED_TRAILERS:
                value = getattr(t, key)
                if value is None or value == "":
                    return ValidationResult.fail(
                        f"C5: commit {commit.short_sha} missing trailer "
                        f"'{key.replace('_', '-').title()}'"
                    )
            if not SESSION_PATTERN.fullmatch(t.agent_session or ""):
                return ValidationResult.fail(
                    f"C5: commit {commit.short_sha} has malformed "
                    f"Agent-Session '{t.agent_session}' "
                    f"(expected s_ plus 4-16 lowercase "
                    f"alphanumeric/hyphen characters, bounded by "
                    f"alphanumeric characters)"
                )
            if not TASK_REF_PATTERN.fullmatch(t.task_ref or ""):
                return ValidationResult.fail(
                    f"C5: commit {commit.short_sha} has malformed "
                    f"Task-Ref '{t.task_ref}' (expected Issue#N | issue#N "
                    f"| PR#N | none | round-N/topic | bot/topic)"
                )
        return ValidationResult.ok()
