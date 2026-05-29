"""Module 2 — branch_supervisor.

Runs L2 (post-merge re-validation) + L5 (break-glass auditor) per supervised
repo per cron tick. Operates on ``main`` HEAD, not on open PRs.

Watermarks: a single file ``bot-state/branch_supervisor_watermarks.json`` in
the bot's own repo tracks the last commit each supervised repo has been
processed up to. Re-processing the same commit on every tick would be O(N)
on commit count; the watermark makes it O(delta).

For now we only implement L5 break-glass detection. L2 (post-merge
re-validation) is a follow-up since it requires re-running the L1
validator set against a merged SHA, which needs the same machinery as
pr_validator.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from multiagent_protocol.github_api import GitHubAPI
from multiagent_protocol.skills.base import BranchHook
from multiagent_protocol.trailers import parse_trailers
from multiagent_protocol.types import CommitContext

logger = logging.getLogger(__name__)

WATERMARKS_PATH = Path("bot-state/branch_supervisor_watermarks.json")


@dataclass(frozen=True)
class SupervisorIncident:
    """One incident the supervisor wants to surface as a GitHub issue."""

    commit_sha: str
    label: str
    body: str


def load_watermarks(path: Path = WATERMARKS_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_watermarks(watermarks: dict[str, str], path: Path = WATERMARKS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(watermarks, indent=2, sort_keys=True), encoding="utf-8")


def scan_repo(
    api: GitHubAPI,
    owner: str,
    repo: str,
    hooks: list[BranchHook],
    watermarks: dict[str, str],
) -> tuple[list[SupervisorIncident], str | None]:
    """Scan ``main`` for new commits since the watermark, run every hook.

    Returns ``(incidents, new_watermark)``. The caller is responsible for
    persisting the new watermark and opening/labelling issues for each
    incident.
    """
    repo_key = f"{owner}/{repo}"
    since = watermarks.get(repo_key)

    raw_commits = api.list_commits_on_main(owner, repo, since_sha=since)
    if not raw_commits:
        return [], since

    new_watermark = raw_commits[0]["sha"]  # newest first per GitHub default

    commits = [_to_commit_context(c) for c in reversed(raw_commits)]  # oldest first

    incidents: list[SupervisorIncident] = []
    for commit in commits:
        for hook in hooks:
            try:
                result = hook.on_commit(commit)
            except Exception as e:
                logger.error(
                    "hook '%s' raised on commit %s: %s",
                    hook.name, commit.short_sha, e,
                )
                continue
            if result.incident_label is not None:
                incidents.append(SupervisorIncident(
                    commit_sha=commit.sha,
                    label=result.incident_label,
                    body=result.incident_body or "",
                ))

    return incidents, new_watermark


def _to_commit_context(raw: dict) -> CommitContext:
    msg = raw["commit"]["message"]
    subject, _, body = msg.partition("\n")
    # GitHub returns the commit author/committer time under
    # commit.committer.date (ISO-8601 UTC). The committer date is the
    # one we want for break-glass deadline checks — author date can be
    # rewritten by `git commit --amend --date=...` whereas committer
    # date reflects when the push actually landed.
    committed_at = ((raw.get("commit") or {}).get("committer") or {}).get("date")
    return CommitContext(
        sha=raw["sha"],
        subject=subject,
        body=body.lstrip("\n"),
        author_login=(raw.get("author") or {}).get("login"),
        committer_login=(raw.get("committer") or {}).get("login"),
        parents=tuple(p["sha"] for p in raw.get("parents", [])),
        trailers=parse_trailers(msg),
        committed_at=committed_at,
    )


# ---------------------------------------------------------------------------
# L2 — post-merge re-validation
#
# For each commit on ``main`` newer than the L2 watermark, re-run the required
# checks against the merged SHA (``docs/concepts/architecture.md`` § "L2").
# A *real* failure opens a ``decision:post-merge-revalidation`` incident; an
# *infra* failure (cancelled / zero-duration) is left unsettled so the next
# tick retries it; ``skipped`` is an intentional protocol skip and passes.
#
# This v0.2.0 implementation is **detection + incident**. Opening a revert PR
# automatically (the architecture's eventual goal) is deferred for the same
# reason auto-cascade is: it has the bot author commits in a supervised repo,
# which is itself a Quadrant-D action that needs its own ADR + integration
# tests. Until then the incident issue carries the exact revert command.
# ---------------------------------------------------------------------------

# Conclusions that mean "the check did not really run" (not a code failure).
_INFRA_CONCLUSIONS = {"cancelled"}
# Conclusions that count as passing for L2 purposes.
_PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}


def _is_infra_failure(check: dict) -> bool:
    """True iff a non-passing check looks like infrastructure, not code.

    Per doctrine: ``cancelled`` (workflow killed mid-run, e.g. Actions-minutes
    exhaustion) or zero-duration (``started_at == completed_at`` → the runner
    queue rejected it, it never executed). ``skipped`` is NOT infra — it means
    the workflow's own ``if:`` evaluated false (an intentional protocol skip).
    """
    if check.get("conclusion") in _INFRA_CONCLUSIONS:
        return True
    started, completed = check.get("started_at"), check.get("completed_at")
    if started and completed and started == completed:
        return True
    return False


def _classify_commit_checks(
    api: GitHubAPI,
    owner: str,
    repo: str,
    sha: str,
    required_checks: tuple[str, ...],
) -> tuple[str, list[str]]:
    """Return ``(status, failing_names)`` for one merged commit.

    ``status`` is ``"passed"``, ``"real_failure"``, or ``"infra"``.
    """
    checks = api.check_runs(owner, repo, sha)
    relevant = [
        c for c in checks
        if not required_checks or c.get("name") in required_checks
    ]
    real: list[str] = []
    infra = False
    for c in relevant:
        if c.get("status") != "completed":
            continue
        if c.get("conclusion") in _PASSING_CONCLUSIONS:
            continue
        if _is_infra_failure(c):
            infra = True
        else:
            real.append(c.get("name", "?"))
    if real:
        return "real_failure", real
    if infra:
        return "infra", []
    return "passed", []


def revalidate_main(
    api: GitHubAPI,
    owner: str,
    repo: str,
    required_checks: tuple[str, ...],
    watermarks: dict[str, str],
    *,
    l2_key_suffix: str = ":l2",
) -> tuple[list[SupervisorIncident], str | None]:
    """L2: re-validate merged commits on ``main`` since the L2 watermark.

    Returns ``(incidents, new_watermark)``. The watermark advances only past
    *settled* commits (passed, or real-failure incident raised). A commit with
    only infra-failures is left unsettled (the watermark stops before it) so a
    later tick re-checks it once the runner recovers.
    """
    repo_key = f"{owner}/{repo}{l2_key_suffix}"
    since = watermarks.get(repo_key)

    raw_commits = api.list_commits_on_main(owner, repo, since_sha=since)
    if not raw_commits:
        return [], since

    incidents: list[SupervisorIncident] = []
    new_watermark = since
    for raw in reversed(raw_commits):  # oldest first
        sha = raw["sha"]
        status, failing = _classify_commit_checks(api, owner, repo, sha, required_checks)
        if status == "infra":
            # Unsettled — do not advance past this commit; retry next tick.
            break
        if status == "real_failure":
            incidents.append(SupervisorIncident(
                commit_sha=sha,
                label="decision:post-merge-revalidation",
                body=_l2_incident_body(owner, repo, sha, failing),
            ))
        new_watermark = sha  # settled (passed or incident-raised)

    return incidents, new_watermark


def _l2_incident_body(owner: str, repo: str, sha: str, failing: list[str]) -> str:
    checks = ", ".join(f"`{n}`" for n in failing) or "(unknown)"
    return (
        f"**Post-merge re-validation failed** on `{owner}/{repo}` at "
        f"`{sha[:7]}`.\n\n"
        f"Failing required check(s): {checks}\n\n"
        f"These checks passed (or were absent) at merge time but fail on the "
        f"merged commit — a real regression on `main`, not an infrastructure "
        f"blip (cancelled / never-run checks are ignored).\n\n"
        f"To restore a known-good `main`, revert the commit:\n\n"
        f"```\ngit revert {sha}\n```\n\n"
        f"then open a PR — label it `decision:auto-revert` so the classifier "
        f"fast-tracks it (Quadrant C). Automatic revert-PR creation is a "
        f"planned enhancement (see `docs/concepts/architecture.md` § L2)."
    )
