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
    return CommitContext(
        sha=raw["sha"],
        subject=subject,
        body=body.lstrip("\n"),
        author_login=(raw.get("author") or {}).get("login"),
        committer_login=(raw.get("committer") or {}).get("login"),
        parents=tuple(p["sha"] for p in raw.get("parents", [])),
        trailers=parse_trailers(msg),
    )
