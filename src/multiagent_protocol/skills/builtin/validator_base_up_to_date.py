"""C4 — PR's base SHA equals the supervised repo's ``main`` HEAD.

Built-in, P0 severity. Prevents merging a PR against a stale base. The L3
race-guard re-checks this just before the actual merge API call, but C4
catches the common case where the PR has been sitting for hours while main
moved.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
    Validator,
)


class BaseUpToDateValidator:
    name = "validator_base_up_to_date"
    severity = "P0"

    def __init__(self, main_head_sha_lookup: callable | None = None) -> None:
        # Caller injects a function that returns the current main HEAD SHA
        # for ``pr_context.full_name``. This decouples the validator from the
        # GitHub API client so it remains a pure function for testing.
        self._main_head_sha_lookup = main_head_sha_lookup

    def check(self, pr_context: PRContext) -> ValidationResult:
        if self._main_head_sha_lookup is None:
            # No lookup provided → cannot check. Treat as pass (skip).
            # This is appropriate during tests; production runs always inject.
            return ValidationResult.ok()
        main_head = self._main_head_sha_lookup(pr_context.full_name)
        if main_head != pr_context.base_sha:
            return ValidationResult.fail(
                f"C4: PR base {pr_context.base_sha[:7]} is stale "
                f"(main HEAD is {main_head[:7]}). Rebase the PR."
            )
        return ValidationResult.ok()
