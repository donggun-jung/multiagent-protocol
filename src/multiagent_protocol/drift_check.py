"""Module 4 — drift_check.

Detects byte-for-byte drift on canonical paths between the governance repo
and each adopter repo. Detection only — no auto-cascade (see
``docs/concepts/mirror-cascade.md`` for the rationale and the planned
opt-in auto-cascade feature).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from multiagent_protocol.github_api import GitHubAPI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MirrorConfig:
    """Loaded from ``schemas/mirror_paths.json`` in the governance repo."""

    canonical_paths: tuple[str, ...]
    exceptions: dict[str, tuple[str, ...]]


def load_mirror_config(path: Path) -> MirrorConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return MirrorConfig(
        canonical_paths=tuple(raw.get("canonical_paths", [])),
        exceptions={
            adopter: tuple(paths)
            for adopter, paths in (raw.get("exceptions") or {}).items()
        },
    )


@dataclass(frozen=True)
class DriftIncident:
    """One drift finding for a single canonical path in a single adopter."""

    adopter_full_name: str
    path: str
    kind: str  # "differs" or "missing"
    canonical_sha: str | None
    adopter_sha: str | None


def check_repo_against_canonical(
    api: GitHubAPI,
    governance_owner: str,
    governance_repo: str,
    adopter_owner: str,
    adopter_repo: str,
    config: MirrorConfig,
) -> list[DriftIncident]:
    """Compare every canonical path between governance and one adopter."""
    incidents: list[DriftIncident] = []
    adopter_full = f"{adopter_owner}/{adopter_repo}"
    excepted = set(config.exceptions.get(adopter_repo, ()))

    for path in config.canonical_paths:
        if path in excepted:
            continue
        canonical_sha = api.get_file_sha256(governance_owner, governance_repo, path)
        if canonical_sha is None:
            # Canonical file does not exist in governance — config bug, but we
            # do not crash. Report as a per-path incident the operator must
            # resolve in their mirror_paths.json.
            incidents.append(DriftIncident(
                adopter_full_name=f"{governance_owner}/{governance_repo}",
                path=path,
                kind="canonical_missing",
                canonical_sha=None,
                adopter_sha=None,
            ))
            continue

        adopter_sha = api.get_file_sha256(adopter_owner, adopter_repo, path)
        if adopter_sha is None:
            incidents.append(DriftIncident(
                adopter_full_name=adopter_full,
                path=path,
                kind="missing",
                canonical_sha=canonical_sha,
                adopter_sha=None,
            ))
        elif adopter_sha != canonical_sha:
            incidents.append(DriftIncident(
                adopter_full_name=adopter_full,
                path=path,
                kind="differs",
                canonical_sha=canonical_sha,
                adopter_sha=adopter_sha,
            ))

    return incidents


def incidents_to_issue_body(incidents: list[DriftIncident]) -> str:
    """Render a single Issue body summarizing one tick's drift findings."""
    if not incidents:
        return "No drift detected."

    by_adopter: dict[str, list[DriftIncident]] = {}
    for inc in incidents:
        by_adopter.setdefault(inc.adopter_full_name, []).append(inc)

    lines = ["**Mirror drift detected.**", ""]
    for adopter, items in sorted(by_adopter.items()):
        lines.append(f"### {adopter}")
        differs = [i for i in items if i.kind == "differs"]
        missing = [i for i in items if i.kind == "missing"]
        canonical_missing = [i for i in items if i.kind == "canonical_missing"]
        if differs:
            lines.append("Differs:")
            for i in differs:
                lines.append(f"- `{i.path}` (canonical sha={i.canonical_sha[:12] if i.canonical_sha else '?'}, adopter={i.adopter_sha[:12] if i.adopter_sha else '?'})")
        if missing:
            lines.append("Missing:")
            for i in missing:
                lines.append(f"- `{i.path}`")
        if canonical_missing:
            lines.append("Canonical config bug (listed as canonical but file missing in governance):")
            for i in canonical_missing:
                lines.append(f"- `{i.path}`")
        lines.append("")
    lines.append(
        "Run the cascade workflow to resolve. See "
        "`docs/concepts/mirror-cascade.md`."
    )
    return "\n".join(lines)
