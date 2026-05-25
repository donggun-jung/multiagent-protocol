"""Publisher-identity gate for ``classifier-judgment`` check-runs.

Without this, any GitHub Actions workflow in any repo could publish a
check-run named ``classifier-judgment`` with summary ``Quadrant: A`` and the
bot's auto-approval path would honor it. This validator refuses to read the
quadrant from a check-run not published by the canonical actor.

Built-in, P0. See ``docs/concepts/general-preferences.md`` §
"6. classifier-judgment must be published by a canonical actor".
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
    Validator,
)

# Default publisher: the GitHub Actions default runner App slug. Operators
# who publish classifier-judgment from a different App should set
# ``config.classifier.publisher_slug``.
DEFAULT_PUBLISHER_SLUG = "github-actions"


class ClassifierPublisherValidator:
    name = "validator_classifier_publisher"
    severity = "P0"

    def __init__(self, publisher_slug: str | None = None) -> None:
        self.publisher_slug = publisher_slug or DEFAULT_PUBLISHER_SLUG

    def check(self, pr_context: PRContext) -> ValidationResult:
        # The validator does not insist on classifier-judgment existing;
        # absent classifier means the classifier engine treats the PR as
        # Quadrant D (owner approval required). What we DO insist on is
        # that, if classifier-judgment is present, it was published by the
        # canonical actor.
        for check in pr_context.check_runs:
            if check.name != "classifier-judgment":
                continue
            if check.app_slug is None or check.app_slug == "":
                return ValidationResult.fail(
                    "classifier-judgment: missing 'app' field "
                    "(cannot verify publisher identity → treating as untrusted)"
                )
            if check.app_slug != self.publisher_slug:
                return ValidationResult.fail(
                    f"classifier-judgment: published by '{check.app_slug}', "
                    f"expected '{self.publisher_slug}'. "
                    f"Treating as untrusted (Quadrant D)."
                )
        return ValidationResult.ok()
