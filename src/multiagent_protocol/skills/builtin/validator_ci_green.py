"""C2 — every required CI check on the PR head SHA has ``conclusion = success``.

Built-in, P0 severity. The set of required checks is derived from the
supervised repo's GitHub branch-protection settings if any, plus an explicit
``required_checks`` field in the project's config entry.

**Publisher trust.** A check-run name alone is not an identity: any GitHub App
installed on the repo can publish a check-run named ``test`` with
``conclusion = success``. When ``expected_check_publisher`` is set, a named
required check is satisfied only by a run published by that App; a same-named
green run from any other publisher is treated as "not yet green" (fail
closed). On the legacy path (no named required checks, the strict
all-completed-checks-succeed mode) the same rule applies to the green signal
as a whole: at least one SUCCESS run must come from the expected App, so a
foreign App's green cannot satisfy C2 by itself. A head with zero check-runs
under ``allow_no_checks`` still passes vacuously (no run to attribute). This
mirrors the publisher-identity gate in :mod:`validator_classifier_publisher`.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    PRContext,
    ValidationResult,
)

# Default publisher of a repo's own CI check-runs: the GitHub Actions default
# runner App slug (the same default as the classifier-judgment publisher).
# Operators whose CI runs under a different App should override this.
DEFAULT_CHECK_PUBLISHER = "github-actions"


def publisher_satisfied(app_slugs, expected_publisher: str | None) -> bool:
    """True iff the expected publisher is among ``app_slugs`` (or none expected).

    The shared publisher-trust primitive: a check signal counts only if at
    least one contributing run was published by ``expected_publisher``. A run
    with no app slug cannot prove its publisher and never satisfies the gate
    (fail closed). With ``expected_publisher`` unset (None) the gate is skipped
    (legacy / publisher-agnostic). Reused by C2 (pre-merge) and L2 (post-merge)
    so a foreign App's green is rejected identically on both sides of the merge.
    """
    if not expected_publisher:
        return True
    return any(slug == expected_publisher for slug in app_slugs)


class CiGreenValidator:
    name = "validator_ci_green"
    severity = "P0"

    def __init__(self, required_checks: tuple[str, ...] | None = None,
                 allow_no_checks: bool = False,
                 expected_check_publisher: str | None = None) -> None:
        # If empty, the validator passes only if *all* completed check-runs
        # are success (strict). If a list is provided, only those named
        # checks must be present + green.
        # ``allow_no_checks`` (env.yml ``allow_no_ci``) lets a head with ZERO
        # check-runs pass C2 vacuously — for repos that have no CI by design.
        # ``expected_check_publisher`` is the App slug that must have published
        # a required check for it to count as green (per-repo configurable via
        # the runtime, default ``github-actions``). ``None`` skips the
        # publisher gate (direct construction / legacy behavior).
        self.required_checks = required_checks or ()
        self.allow_no_checks = allow_no_checks
        self.expected_check_publisher = expected_check_publisher

    def check(self, pr_context: PRContext) -> ValidationResult:
        if self.required_checks:
            # R1 fail-closed: a required check is PRESENT-AND-GREEN only if it
            # has >=1 run AND NO run of that name is non-success. We must inspect
            # ALL same-named runs (not a ``{name: check}`` map, which would
            # collapse a [failure, success] pair to the success and mask the
            # failure — a fail-OPEN bypass). Any failing/incomplete same-name run
            # fails C2.
            for required in self.required_checks:
                runs = [c for c in pr_context.check_runs if c.name == required]
                if not runs:
                    return ValidationResult.fail(
                        f"C2: required check '{required}' is missing"
                    )
                bad = self._first_non_success(runs)
                if bad is not None:
                    return bad
                # Publisher trust: a green required check counts only if at
                # least one of its runs was published by the expected App.
                # A same-named run from a different (or missing) publisher is
                # "not yet green" — fail closed, mirroring the identity gate
                # in validator_classifier_publisher. Foreign non-success runs
                # were already rejected above (we never RELAX on identity).
                if not publisher_satisfied(
                    (c.app_slug for c in runs), self.expected_check_publisher
                ):
                    seen = sorted(
                        {c.app_slug or "<missing app>" for c in runs}
                    )
                    return ValidationResult.fail(
                        f"C2: required check '{required}' was not published by "
                        f"the expected app '{self.expected_check_publisher}' "
                        f"(published by: {', '.join(seen)}). "
                        f"Treating as not yet green."
                    )
            return ValidationResult.ok()

        # No explicit list: every completed check must be success.
        if not pr_context.check_runs:
            if self.allow_no_checks:
                return ValidationResult.ok()  # repo has no CI by design (opt-in)
            return ValidationResult.fail(
                "C2: no check-runs found on PR head (CI may not have started). "
                "If this repo has no CI by design, set env.yml `allow_no_ci: true`."
            )
        # Iterate EVERY run (never a name-deduped map): a duplicate failing check
        # must not be masked by a same-named success.
        bad = self._first_non_success(pr_context.check_runs, allow_neutral=True)
        if bad is not None:
            return bad
        # Publisher trust on the legacy path too: with no named required
        # checks the "green" signal is whatever success runs are present, so a
        # foreign App's green must not satisfy C2 by itself. When a publisher
        # is expected, require at least one SUCCESS run from it (fail closed,
        # mirroring the named-required path and validator_classifier_publisher).
        if not publisher_satisfied(
            (c.app_slug for c in pr_context.check_runs if c.conclusion == "success"),
            self.expected_check_publisher,
        ):
            seen = sorted({c.app_slug or "<missing app>" for c in pr_context.check_runs})
            return ValidationResult.fail(
                f"C2: no green check-run was published by the expected app "
                f"'{self.expected_check_publisher}' (publishers seen: "
                f"{', '.join(seen)}). Treating as not yet green."
            )
        return ValidationResult.ok()

    @staticmethod
    def _first_non_success(checks, *, allow_neutral: bool = False):
        """Return a failing ValidationResult for the first non-success run, else None.

        A run is acceptable iff it is ``completed`` with conclusion ``success``
        — or ``neutral`` when ``allow_neutral``. ``neutral`` is allowed only in
        the unnamed all-checks path because SIGNAL check-runs (e.g. the
        ``classifier-judgment`` the engine itself publishes with conclusion
        ``neutral``/``action_required``) are advisory, not pass/fail CI; treating
        them as failures would block every PR. A NAMED ``required_check`` does
        NOT pass on ``neutral`` (the required path calls this without
        ``allow_neutral``), so an operator who names a check still gets strict
        success-only. Any other run — incomplete, failed, cancelled, timed out,
        skipped — fails C2.
        """
        for check in checks:
            if check.status != "completed":
                return ValidationResult.fail(
                    f"C2: check '{check.name}' not yet completed "
                    f"(status={check.status})"
                )
            if check.conclusion == "success":
                continue
            if allow_neutral and check.conclusion == "neutral":
                continue
            return ValidationResult.fail(
                f"C2: check '{check.name}' did not succeed "
                f"(conclusion={check.conclusion})"
            )
        return None
