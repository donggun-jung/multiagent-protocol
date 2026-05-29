"""L4 — identity gate: validate ``Agent-*`` trailer VALUES against the registry.

C5 (``validator_trailers``) checks trailer **format**. This validator checks
that each commit's ``(Agent-Tool, Agent-Model)`` pair is declared in the
operator's ``config/agent_registry.yml``.

Per the L4 burn-in doctrine (``docs/concepts/four-quadrants.md`` § "L4 burn-in:
60-day advisory window"), a newly-added agent vendor/model is **advisory**
(warns, does not block) before promotion to hard-block. This validator
therefore ships at severity ``P2`` (warn, do not block). An operator who wants
a hard identity gate today promotes it to ``P0`` via ``config/skills.yml``
``severity_overrides``. The automatic 60-day burn-in promotion ships later.

Unknown **machines** are deliberately not failed: the registry treats unlisted
machines as "passes the gate but earns no extra trust signals" (same doctrine
section). Only the tool/model pair is checked here.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
)


class AgentRegistryValidator:
    name = "validator_agent_registry"
    severity = "P2"  # advisory; promote to P0 via config/skills.yml severity_overrides

    def __init__(self, registry=None) -> None:
        # ``registry`` is an ``AgentRegistry`` (see config.loader). When None
        # — the 0-arg instance the skills loader builds, or when the operator
        # ships no agent_registry.yml — this validator is a no-op: there is
        # nothing to check against. The orchestrator injects the real registry.
        self.registry = registry

    def check(self, pr_context: PRContext) -> ValidationResult:
        if self.registry is None:
            return ValidationResult.ok()
        for commit in pr_context.commits:
            t = commit.trailers
            tool = t.agent_tool
            if tool is None or tool == "":
                # Trailer *format* (presence) is C5's responsibility; L4 only
                # judges the values when they are present.
                continue
            if not self.registry.model_allowed(tool, t.agent_model):
                return ValidationResult.fail(
                    f"L4: commit {commit.short_sha} declares Agent-Tool "
                    f"'{tool}' / Agent-Model '{t.agent_model}', not present in "
                    f"the agent registry. Add the tool/model to "
                    f"config/agent_registry.yml, or treat this as an "
                    f"unrecognized identity."
                )
        return ValidationResult.ok()
