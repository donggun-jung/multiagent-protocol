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
from multiagent_protocol.label_provenance import approval_receipt_comment

logger = logging.getLogger(__name__)

PENDING_LABEL = "decision:pending-owner"

# Ballot A/B = approve (merge); C = defer (needs more info, do NOT merge);
# reject = close. See docs/concepts/four-quadrants.md § "Quadrant D".
Verdict = Literal["approved-A", "approved-B", "approved-C", "rejected", "deferred"]

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
            # Ballot C = "defer / needs more info" (doctrine) — NOT a merge.
            events.append((ts, "deferred" if letter == "C" else f"approved-{letter}"))

    if not events:
        return None
    # Most-recent timestamp wins. ISO-8601 lexicographic sort works.
    events.sort(key=lambda x: x[0])
    return events[-1][1]  # type: ignore[return-value]


# -- Orchestration: poll open inbox issues → apply the owner's verdict --

PR_REF_RE = re.compile(r"PR:\s*`([^`#]+)#(\d+)`")


def parse_pr_ref(issue_body_text: str) -> tuple[str, int] | None:
    """Read the ``- PR: `owner/repo#N``` line from an inbox issue body.

    Returns ``(full_name, number)`` or ``None`` if the line is absent.
    """
    m = PR_REF_RE.search(issue_body_text)
    if not m:
        return None
    return m.group(1), int(m.group(2))


@dataclass(frozen=True)
class InboxResolution:
    """One inbox issue resolved (or refused) during a tick."""

    issue_number: int
    pr_full_name: str
    pr_number: int
    verdict: str   # "approved-A" | "approved-B" | "approved-C" | "rejected" | "tampered"
    action: str    # "labeled" | "closed-pr" | "tamper-skip"


def resolve_open_issues(
    api: GitHubAPI,
    governance_owner: str,
    governance_repo: str,
    allowlisted_actors: tuple[str, ...],
) -> list[InboxResolution]:
    """Poll open ``decision:pending-owner`` issues and apply owner verdicts.

    For each issue with an owner verdict (reaction/comment by an allowlisted
    actor): verify the PR head still matches the SHA the decision was opened
    against (tamper guard), then either label the PR ``decision:approved-*``
    (so L1.C3 passes on the next tick and the PR merges) or close the PR on
    ``/reject``. The inbox issue is closed once the verdict is applied.

    Returns the list of resolutions taken (for tick metrics + logging).
    """
    resolutions: list[InboxResolution] = []
    issues = api.list_issues(
        governance_owner, governance_repo, labels=PENDING_LABEL, state="open"
    )
    for issue in issues:
        # The issues endpoint also returns PRs; skip anything that is a PR.
        if "pull_request" in issue:
            continue
        body = issue.get("body") or ""
        issue_number = issue["number"]
        _nonce, head_sha = parse_nonce_and_sha(body)
        pr_ref = parse_pr_ref(body)
        if pr_ref is None or not head_sha:
            continue
        pr_full_name, pr_number = pr_ref
        pr_owner, _, pr_repo = pr_full_name.partition("/")

        reactions = api.list_issue_reactions(governance_owner, governance_repo, issue_number)
        comments = api.list_issue_comments(governance_owner, governance_repo, issue_number)
        verdict = resolve_verdict(reactions, comments, allowlisted_actors)
        if verdict is None:
            continue

        # Tamper guard: the PR head must still equal the SHA we asked about.
        try:
            pr = api.pr(pr_owner, pr_repo, pr_number)
        except Exception as e:
            logger.warning("inbox: could not fetch PR %s: %s", pr_full_name, e)
            continue
        current_head = (pr.get("head") or {}).get("sha")
        if current_head != head_sha:
            api.post_comment(
                governance_owner, governance_repo, issue_number,
                f"⚠️ PR head changed since this decision opened "
                f"(`{head_sha[:7]}` → `{(current_head or '?')[:7]}`). The prior "
                f"approval is void; the bot will re-evaluate the new head. "
                f"Re-approve against the new head if still desired.",
            )
            resolutions.append(InboxResolution(
                issue_number, pr_full_name, pr_number, "tampered", "tamper-skip",
            ))
            continue

        if verdict == "rejected":
            api.post_comment(
                pr_owner, pr_repo, pr_number,
                "Closed per Decision Inbox `/reject` (owner rejected this PR).",
            )
            api.close_issue(pr_owner, pr_repo, pr_number)  # PRs close via the issues endpoint
            api.close_issue(governance_owner, governance_repo, issue_number)
            resolutions.append(InboxResolution(
                issue_number, pr_full_name, pr_number, verdict, "closed-pr",
            ))
        elif verdict == "deferred":
            # Ballot C — defer / needs more info. Do NOT merge. Label the PR and
            # leave the inbox issue OPEN so the owner can later flip to
            # `/approve A|B` or `/reject`. Idempotent: skip if already deferred.
            pr_labels = {(lbl.get("name")) for lbl in (pr.get("labels") or [])}
            if "decision:deferred" in pr_labels:
                continue
            api.add_label(pr_owner, pr_repo, pr_number, "decision:deferred")
            api.post_comment(
                governance_owner, governance_repo, issue_number,
                "Deferred per `/approve C` (needs more info). The PR is **not** "
                "merged; re-decide with 👍 / `/approve [A|B]` / `/reject`.",
            )
            resolutions.append(InboxResolution(
                issue_number, pr_full_name, pr_number, verdict, "deferred",
            ))
        else:
            # approved-A/B → label the PR so owner_approval (C3) passes, and
            # post the SHA receipt binding the approval to the exact head it
            # was verified against (head_sha == current_head here). C3 honours
            # the label only while the PR head still equals this SHA.
            label = f"decision:{verdict}"
            api.add_label(pr_owner, pr_repo, pr_number, label)
            api.post_comment(
                pr_owner, pr_repo, pr_number,
                approval_receipt_comment(label, head_sha),
            )
            api.close_issue(governance_owner, governance_repo, issue_number)
            resolutions.append(InboxResolution(
                issue_number, pr_full_name, pr_number, verdict, "labeled",
            ))

    return resolutions
