"""C5 — every commit in the PR has well-formed ``Agent-*`` + ``Task-Ref`` trailers.

Built-in, P0 severity. Cannot be disabled via ``config.skills.disabled``;
only severity can be overridden. See ``docs/concepts/general-preferences.md``
§ "3. Agent-* commit trailers required".
"""

from __future__ import annotations

import re

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
)

SESSION_PATTERN = re.compile(r"^s_[a-z0-9-]{2,14}[a-z0-9]$")
TASK_REF_PATTERN = re.compile(
    r"^(Issue#\d+|PR#\d+|none|round-\d+/[A-Za-z0-9\-/_.]+|bot/[A-Za-z0-9\-/_.]+)$"
)


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
            if not SESSION_PATTERN.match(t.agent_session or ""):
                return ValidationResult.fail(
                    f"C5: commit {commit.short_sha} has malformed "
                    f"Agent-Session '{t.agent_session}' "
                    f"(expected pattern s_<2-14 alphanumeric/hyphen>"
                    f"<alphanumeric>)"
                )
            if not TASK_REF_PATTERN.match(t.task_ref or ""):
                return ValidationResult.fail(
                    f"C5: commit {commit.short_sha} has malformed "
                    f"Task-Ref '{t.task_ref}' (expected Issue#N | PR#N "
                    f"| none | round-N/topic | bot/topic)"
                )
        return ValidationResult.ok()
