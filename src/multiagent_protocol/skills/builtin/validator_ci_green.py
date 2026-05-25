"""C2 — every required CI check on the PR head SHA has ``conclusion = success``.

Built-in, P0 severity. The set of required checks is derived from the
supervised repo's GitHub branch-protection settings if any, plus an explicit
``required_checks`` field in the project's config entry.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
)


class CiGreenValidator:
    name = "validator_ci_green"
    severity = "P0"

    def __init__(self, required_checks: tuple[str, ...] | None = None) -> None:
        # If empty, the validator passes only if *all* completed check-runs
        # are success (strict). If a list is provided, only those named
        # checks must be present + green.
        self.required_checks = required_checks or ()

    def check(self, pr_context: PRContext) -> ValidationResult:
        by_name = {c.name: c for c in pr_context.check_runs}

        if self.required_checks:
            for required in self.required_checks:
                check = by_name.get(required)
                if check is None:
                    return ValidationResult.fail(
                        f"C2: required check '{required}' is missing"
                    )
                if check.status != "completed":
                    return ValidationResult.fail(
                        f"C2: required check '{required}' not yet completed "
                        f"(status={check.status})"
                    )
                if check.conclusion != "success":
                    return ValidationResult.fail(
                        f"C2: required check '{required}' did not succeed "
                        f"(conclusion={check.conclusion})"
                    )
            return ValidationResult.ok()

        # No explicit list: every completed check must be success.
        if not pr_context.check_runs:
            return ValidationResult.fail(
                "C2: no check-runs found on PR head (CI may not have started)"
            )
        for check in pr_context.check_runs:
            if check.status != "completed":
                return ValidationResult.fail(
                    f"C2: check '{check.name}' not yet completed "
                    f"(status={check.status})"
                )
            if check.conclusion != "success" and check.conclusion not in ("neutral",):
                # 'neutral' is treated as informational-only and passes.
                return ValidationResult.fail(
                    f"C2: check '{check.name}' did not succeed "
                    f"(conclusion={check.conclusion})"
                )
        return ValidationResult.ok()
