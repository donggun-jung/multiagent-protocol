"""4-quadrant classifier engine.

Runs all registered :class:`ClassifierRule` instances and takes the
**maximum** quadrant (D > B > C > A). User-added rules cannot lower the
verdict — a user voting A is silently ignored if any other rule voted higher.

Audit-logs every decision to JSONL. See ``docs/concepts/four-quadrants.md``
for full semantics.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from multiagent_protocol.skills.base import ClassifierRule, ClassifierVote
from multiagent_protocol.types import PRContext, Quadrant

QUADRANT_ORDER: dict[Quadrant, int] = {"A": 0, "C": 1, "B": 2, "D": 3}


@dataclass(frozen=True)
class ClassifierVerdict:
    """The final classifier output for one PR."""

    quadrant: Quadrant
    votes: tuple[tuple[str, Quadrant, str], ...]  # (rule_name, quadrant, reasoning)
    pr_full_name: str
    pr_number: int
    head_sha: str
    timestamp: str


def classify(
    pr_context: PRContext,
    rules: Iterable[ClassifierRule],
    audit_log_path: Path | None = None,
) -> ClassifierVerdict:
    """Run all rules, return the maximum-quadrant verdict.

    If ``audit_log_path`` is provided, the verdict is appended to that file
    as one JSONL row.
    """
    votes: list[tuple[str, Quadrant, str]] = []
    for rule in rules:
        vote: ClassifierVote = rule.evaluate(pr_context)
        votes.append((rule.name, vote.quadrant, vote.reasoning))

    final_quadrant = _maximum_quadrant(v[1] for v in votes)

    verdict = ClassifierVerdict(
        quadrant=final_quadrant,
        votes=tuple(votes),
        pr_full_name=pr_context.full_name,
        pr_number=pr_context.number,
        head_sha=pr_context.head_sha,
        timestamp=_now_utc_iso(),
    )

    if audit_log_path is not None:
        _append_audit_row(verdict, audit_log_path)

    return verdict


def _maximum_quadrant(quadrants: Iterable[Quadrant]) -> Quadrant:
    best: Quadrant = "A"
    best_score = QUADRANT_ORDER["A"]
    for q in quadrants:
        score = QUADRANT_ORDER.get(q, 0)
        if score > best_score:
            best = q
            best_score = score
    return best


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_audit_row(verdict: ClassifierVerdict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": verdict.timestamp,
        "pr": f"{verdict.pr_full_name}#{verdict.pr_number}",
        "head_sha": verdict.head_sha,
        "quadrant": verdict.quadrant,
        "votes": [
            {"rule": name, "quadrant": q, "reasoning": r}
            for name, q, r in verdict.votes
        ],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
