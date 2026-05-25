"""Module 3 — decision_inbox.

Owns the Quadrant D → owner → resume loop. Routes PRs the classifier flags
Quadrant D to GitHub Issues with the structured ballot defined in
``docs/concepts/decision-inbox.md``.

The polling logic counts owner reactions and ``/approve [A|B|C]`` or
``/reject`` comments. Tamper detection: each Issue body embeds the PR's
head-SHA in an HTML comment; if the PR head changes after the Issue opens,
the bot does NOT treat prior approval as valid.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from multiagent_protocol.github_api import GitHubAPI

logger = logging.getLogger(__name__)

PENDING_LABEL = "decision:pending-owner"

Verdict = Literal["approved-A", "approved-B", "approved-C", "rejected"]

APPROVE_RE = re.compile(r"^\s*/approve\s+([ABC])\s*$", re.MULTILINE | re.IGNORECASE)
REJECT_RE = re.compile(r"^\s*/reject\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class OpenedIssue:
    """Result of opening a Decision Inbox issue for a Quadrant D PR."""

    issue_number: int
    nonce: str
    head_sha: str


def issue_body(
    pr_full_name: str,
    pr_number: int,
    head_sha: str,
    classifier_reasoning: str,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Build the structured Decision Inbox issue body.

    Schema documented in ``docs/concepts/decision-inbox.md`` §
    "Issue body schema".
    """
    if nonce is None:
        nonce = uuid.uuid4().hex
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    short_sha = head_sha[:7]
    return f"""**Owner approval required (Quadrant D)** — irreversible + critical.

Respond with 👍 (option A / approve), 👎 (reject), `/approve [A|B|C]` / `/reject`,
or tick a checkbox below.

## Options

- [ ] Option A — proceed as recommended
- [ ] Option B — alternate (see PR description)
- [ ] Option C — defer / needs more info

- PR: `{pr_full_name}#{pr_number}` — head `{short_sha}`
- Classifier: Quadrant D
- Reasoning: {classifier_reasoning}
- Opened at: {timestamp}

<!-- decision-inbox-nonce: {nonce} -->
<!-- decision-inbox-head-sha: {head_sha} -->
"""


def open_inbox_issue(
    api: GitHubAPI,
    inbox_owner: str,
    inbox_repo: str,
    pr_full_name: str,
    pr_number: int,
    head_sha: str,
    classifier_reasoning: str,
) -> OpenedIssue:
    nonce = uuid.uuid4().hex
    body = issue_body(
        pr_full_name=pr_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        classifier_reasoning=classifier_reasoning,
        nonce=nonce,
    )
    title = f"Decision pending — PR {pr_full_name}#{pr_number}"
    issue = api.open_issue(
        owner=inbox_owner,
        repo=inbox_repo,
        title=title,
        body=body,
        labels=[PENDING_LABEL],
    )
    return OpenedIssue(
        issue_number=issue["number"],
        nonce=nonce,
        head_sha=head_sha,
    )


def parse_nonce_and_sha(issue_body_text: str) -> tuple[str | None, str | None]:
    """Read embedded nonce + head_sha from an inbox Issue's body.

    Returns ``(nonce, head_sha)`` or ``(None, None)`` if missing.
    """
    nonce = None
    head_sha = None
    for line in issue_body_text.split("\n"):
        s = line.strip()
        if s.startswith("<!-- decision-inbox-nonce:") and s.endswith("-->"):
            nonce = s.removeprefix("<!-- decision-inbox-nonce:").removesuffix("-->").strip()
        elif s.startswith("<!-- decision-inbox-head-sha:") and s.endswith("-->"):
            head_sha = s.removeprefix("<!-- decision-inbox-head-sha:").removesuffix("-->").strip()
    return nonce, head_sha


def resolve_verdict(
    reactions: list[dict],
    comments: list[dict],
    allowlisted_actors: tuple[str, ...],
) -> Verdict | None:
    """Determine the owner's verdict from reactions + comments.

    Takes the most-recent signal across both surfaces. Only reactions and
    comments by users in ``allowlisted_actors`` count.

    Returns one of ``"approved-A"``, ``"approved-B"``, ``"approved-C"``,
    ``"rejected"``, or ``None`` if no verdict yet.
    """
    events: list[tuple[str, str]] = []  # (timestamp, verdict)

    # Reactions: 👍 → approved-A, 👎 → rejected. Other emoji ignored.
    for r in reactions:
        login = (r.get("user") or {}).get("login")
        if login not in allowlisted_actors:
            continue
        content = r.get("content")
        ts = r.get("created_at", "")
        if content == "+1":
            events.append((ts, "approved-A"))
        elif content == "-1":
            events.append((ts, "rejected"))

    # Comments: parse /approve / /reject.
    for c in comments:
        login = (c.get("user") or {}).get("login")
        if login not in allowlisted_actors:
            continue
        body = c.get("body") or ""
        ts = c.get("created_at", "")
        m_app = APPROVE_RE.search(body)
        m_rej = REJECT_RE.search(body)
        if m_rej:
            events.append((ts, "rejected"))
        elif m_app:
            letter = m_app.group(1).upper()
            events.append((ts, f"approved-{letter}"))

    if not events:
        return None
    # Most-recent timestamp wins. ISO-8601 lexicographic sort works.
    events.sort(key=lambda x: x[0])
    return events[-1][1]  # type: ignore[return-value]
