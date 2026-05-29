"""Module 1 — pr_validator.

Runs L1 (5-condition pre-merge gate) + L3 (race guard) + L4 (identity gate)
per open PR per cron tick.

This module builds a :class:`PRContext` from the GitHub API and runs the
registered validators + classifier rules in :mod:`classifier`. It does NOT
itself perform the merge; ``main.py`` decides whether to merge based on the
validator results and the classifier verdict.

The L3 race guard runs **inside** the merge call (the GitHub API's
``sha`` precondition param defeats TOCTOU at the merge moment).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from multiagent_protocol.github_api import GitHubAPI
from multiagent_protocol.skills.base import Validator
from multiagent_protocol.trailers import parse_trailers
from multiagent_protocol.types import (
    CheckRunStatus,
    CommitContext,
    FileChange,
    LabelEvent,
    PRContext,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PRValidationOutcome:
    """Result of running L1 + classifier on a PR.

    Severity-aware: a failing validator blocks the merge only if its severity
    is ``P0`` or ``P1``. ``P2`` failures are surfaced as non-blocking warnings;
    ``P3`` failures are audit-only (recorded in ``results`` but neither block
    nor comment). See ``docs/concepts/general-preferences.md`` § "Severity".
    """

    pr_context: PRContext
    classifier_quadrant: str
    results: tuple[tuple[str, bool, str | None, str], ...]  # (name, passed, reason, severity)
    all_passed: bool                       # no P0/P1 failures
    failure_reasons: tuple[str, ...]       # P0/P1 (blocking) failure reasons
    warnings: tuple[str, ...]              # P2 (non-blocking) failure reasons

    def diagnostic_comment(self) -> str:
        if self.all_passed and not self.warnings:
            return (
                f"Merge Gate L1 — all conditions satisfied "
                f"(Quadrant: {self.classifier_quadrant})."
            )
        lines: list[str] = []
        if not self.all_passed:
            lines.append("Merge Gate L1 — merge blocked:")
            for reason in self.failure_reasons:
                lines.append(f"- {reason}")
        if self.warnings:
            lines.append("")
            lines.append("Warnings (non-blocking):")
            for reason in self.warnings:
                lines.append(f"- {reason}")
        lines.append(
            "\nFix the blocking items above and the bot will re-evaluate on "
            "the next cron tick."
        )
        return "\n".join(lines)


def build_pr_context(api: GitHubAPI, pr_payload: dict) -> PRContext:
    """Construct a PRContext from a GitHub PR JSON payload + supplemental API."""
    owner = pr_payload["base"]["repo"]["owner"]["login"]
    repo = pr_payload["base"]["repo"]["name"]
    number = pr_payload["number"]

    commits_data = api.pr_commits(owner, repo, number)
    files_data = api.pr_files(owner, repo, number)
    checks_data = api.check_runs(owner, repo, pr_payload["head"]["sha"])
    # Label-add events from the timeline API — lets C1 check *who* applied
    # ``ready-to-merge``, not just that it is present.
    label_events: tuple[LabelEvent, ...] = tuple(
        LabelEvent(
            label=le["label"],
            actor_login=le["actor"],
            created_at=le.get("created_at", ""),
        )
        for le in api.label_events(owner, repo, number)
        if le.get("label") and le.get("actor")
    )

    commits = tuple(
        CommitContext(
            sha=c["sha"],
            subject=c["commit"]["message"].split("\n", 1)[0],
            body=c["commit"]["message"].split("\n", 1)[1] if "\n" in c["commit"]["message"] else "",
            author_login=(c.get("author") or {}).get("login"),
            committer_login=(c.get("committer") or {}).get("login"),
            parents=tuple(p["sha"] for p in c.get("parents", [])),
            trailers=parse_trailers(c["commit"]["message"]),
            committed_at=((c.get("commit") or {}).get("committer") or {}).get("date"),
        )
        for c in commits_data
    )

    files = tuple(
        FileChange(
            path=f["filename"],
            status=_normalize_file_status(f.get("status", "modified")),
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
        )
        for f in files_data
    )

    checks = tuple(
        CheckRunStatus(
            name=c["name"],
            status=c["status"],
            conclusion=c.get("conclusion"),
            started_at=c.get("started_at"),
            completed_at=c.get("completed_at"),
            app_slug=(c.get("app") or {}).get("slug"),
            output_summary=(c.get("output") or {}).get("summary"),
        )
        for c in checks_data
    )

    return PRContext(
        repo_owner=owner,
        repo_name=repo,
        number=number,
        title=pr_payload.get("title", ""),
        body=pr_payload.get("body") or "",
        head_sha=pr_payload["head"]["sha"],
        base_sha=pr_payload["base"]["sha"],
        base_ref=pr_payload["base"]["ref"],
        state=pr_payload["state"],
        merged=pr_payload.get("merged", False),
        labels=tuple(label["name"] for label in pr_payload.get("labels", [])),
        author_login=(pr_payload.get("user") or {}).get("login"),
        commits=commits,
        files_changed=files,
        check_runs=checks,
        label_events=label_events,
    )


def _normalize_file_status(s: str) -> str:
    # GitHub uses "modified", "added", "removed", "renamed"; we keep all.
    if s in ("added", "modified", "removed", "renamed"):
        return s
    return "modified"


_BLOCKING_SEVERITIES = ("P0", "P1")


def evaluate_pr(
    pr_context: PRContext,
    validators: list[Validator],
    classifier_quadrant: str = "A",
) -> PRValidationOutcome:
    """Run every validator; bucket failures by severity.

    A failure blocks the merge (``all_passed = False``) only at ``P0``/``P1``.
    ``P2`` failures become non-blocking warnings; ``P3`` failures are recorded
    but neither block nor surface in the diagnostic comment.
    """
    results: list[tuple[str, bool, str | None, str]] = []
    blocking: list[str] = []
    warnings: list[str] = []
    for v in validators:
        severity = getattr(v, "severity", "P0")
        r = v.check(pr_context)
        results.append((v.name, r.passed, r.failure_reason, severity))
        if not r.passed and r.failure_reason:
            if severity in _BLOCKING_SEVERITIES:
                blocking.append(r.failure_reason)
            elif severity == "P2":
                warnings.append(r.failure_reason)
            # P3: audit-only — recorded in results, neither blocks nor warns.

    return PRValidationOutcome(
        pr_context=pr_context,
        classifier_quadrant=classifier_quadrant,
        results=tuple(results),
        all_passed=not blocking,
        failure_reasons=tuple(blocking),
        warnings=tuple(warnings),
    )


def run_l3_race_guard(api: GitHubAPI, pr_context: PRContext) -> bool:
    """Return True iff PR base SHA still equals main HEAD.

    Called immediately before the merge API call. The merge API itself has
    a ``sha`` precondition that defeats TOCTOU, but this function lets us
    skip the merge call entirely on stale base.
    """
    main_head = api.main_head_sha(pr_context.repo_owner, pr_context.repo_name)
    return main_head == pr_context.base_sha
