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

# One-time tamper marker (resolve_open_issues): the "head changed → prior
# approval is void" comment is posted ONCE, then this label on the inbox
# issue suppresses re-posting on every subsequent tick.
STALE_APPROVAL_LABEL = "decision:stale-approval"

# Poison-issue guard (resolve_open_issues): after this many consecutive
# failures processing the same inbox issue, the issue is labelled and gets
# one diagnostic comment so the failure surfaces to the owner.
INBOX_ERROR_LABEL = "decision:inbox-error"
INBOX_ERROR_THRESHOLD = 3

# Consecutive per-issue failure counts, keyed (inbox owner, inbox repo,
# issue number). Module-level so the threshold accumulates across
# resolve_open_issues calls within one process; a stateless cron tick starts
# fresh each run (the per-issue isolation below still protects every tick —
# the escalation fires in longer-lived processes, or when the caller passes
# its own persistent dict).
_inbox_failure_counts: dict[tuple[str, str, int], int] = {}

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


def _label_names(payload: dict) -> set[str]:
    """The label names on an issue/PR payload (GitHub's ``labels`` list shape)."""
    return {lbl.get("name") for lbl in (payload.get("labels") or [])}


def _mark_inbox_error(
    api: GitHubAPI, owner: str, repo: str, issue_number: int,
    count: int, error: Exception,
) -> None:
    """Best-effort escalation for a repeatedly-failing inbox issue.

    Label first (the durable marker that suppresses re-escalation), then ONE
    diagnostic comment. Either call may itself fail on a poisoned issue —
    that is logged; the marker is re-attempted on the next failure past the
    threshold.
    """
    try:
        api.add_label(owner, repo, issue_number, INBOX_ERROR_LABEL)
        api.post_comment(
            owner, repo, issue_number,
            f"⚠️ The bot failed to process this Decision Inbox issue "
            f"{count} times in a row (last error: `{error}`). Other inbox "
            f"issues are unaffected; this one is skipped each tick until "
            f"the underlying problem is fixed.",
        )
    except Exception as e:
        logger.warning(
            "inbox: could not mark issue %s/%s#%s as %s: %s",
            owner, repo, issue_number, INBOX_ERROR_LABEL, e,
        )


def _resolve_issue(
    api: GitHubAPI,
    governance_owner: str,
    governance_repo: str,
    allowlisted_actors: tuple[str, ...],
    issue: dict,
) -> InboxResolution | None:
    """Resolve ONE open inbox issue; ``None`` = nothing to do (yet).

    May raise on an API failure — the per-issue guard in
    :func:`resolve_open_issues` logs it and moves on to the next issue.
    """
    body = issue.get("body") or ""
    issue_number = issue["number"]
    _nonce, head_sha = parse_nonce_and_sha(body)
    pr_ref = parse_pr_ref(body)
    if pr_ref is None or not head_sha:
        return None
    pr_full_name, pr_number = pr_ref
    pr_owner, _, pr_repo = pr_full_name.partition("/")

    reactions = api.list_issue_reactions(governance_owner, governance_repo, issue_number)
    comments = api.list_issue_comments(governance_owner, governance_repo, issue_number)
    verdict = resolve_verdict(reactions, comments, allowlisted_actors)
    if verdict is None:
        return None

    # Tamper guard: the PR head must still equal the SHA we asked about.
    try:
        pr = api.pr(pr_owner, pr_repo, pr_number)
    except Exception as e:
        logger.warning("inbox: could not fetch PR %s: %s", pr_full_name, e)
        return None
    current_head = (pr.get("head") or {}).get("sha")
    if current_head != head_sha:
        # One-time state transition: explain the void approval ONCE, mark the
        # issue with the stale-approval label, and stay silent on every later
        # tick while the marker is present (the stateless tick would otherwise
        # re-post this comment forever). If the head ever returns to the
        # recorded SHA, the verdict path below proceeds normally regardless of
        # the marker.
        if STALE_APPROVAL_LABEL in _label_names(issue):
            return None
        api.post_comment(
            governance_owner, governance_repo, issue_number,
            f"⚠️ PR head changed since this decision opened "
            f"(`{head_sha[:7]}` → `{(current_head or '?')[:7]}`). The prior "
            f"approval is void; the bot will re-evaluate the new head. "
            f"Re-approve against the new head if still desired.",
        )
        api.add_label(
            governance_owner, governance_repo, issue_number, STALE_APPROVAL_LABEL
        )
        return InboxResolution(
            issue_number, pr_full_name, pr_number, "tampered", "tamper-skip",
        )

    if verdict == "rejected":
        api.post_comment(
            pr_owner, pr_repo, pr_number,
            "Closed per Decision Inbox `/reject` (owner rejected this PR).",
        )
        api.close_issue(pr_owner, pr_repo, pr_number)  # PRs close via the issues endpoint
        api.close_issue(governance_owner, governance_repo, issue_number)
        return InboxResolution(
            issue_number, pr_full_name, pr_number, verdict, "closed-pr",
        )
    if verdict == "deferred":
        # Ballot C — defer / needs more info. Do NOT merge. Label the PR and
        # leave the inbox issue OPEN so the owner can later flip to
        # `/approve A|B` or `/reject`. Idempotent: skip if already deferred.
        if "decision:deferred" in _label_names(pr):
            return None
        api.add_label(pr_owner, pr_repo, pr_number, "decision:deferred")
        api.post_comment(
            governance_owner, governance_repo, issue_number,
            "Deferred per `/approve C` (needs more info). The PR is **not** "
            "merged; re-decide with 👍 / `/approve [A|B]` / `/reject`.",
        )
        return InboxResolution(
            issue_number, pr_full_name, pr_number, verdict, "deferred",
        )
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
    return InboxResolution(
        issue_number, pr_full_name, pr_number, verdict, "labeled",
    )


def resolve_open_issues(
    api: GitHubAPI,
    governance_owner: str,
    governance_repo: str,
    allowlisted_actors: tuple[str, ...],
    *,
    failure_counts: dict[tuple[str, str, int], int] | None = None,
) -> list[InboxResolution]:
    """Poll open ``decision:pending-owner`` issues and apply owner verdicts.

    For each issue with an owner verdict (reaction/comment by an allowlisted
    actor): verify the PR head still matches the SHA the decision was opened
    against (tamper guard), then either label the PR ``decision:approved-*``
    (so L1.C3 passes on the next tick and the PR merges) or close the PR on
    ``/reject``. The inbox issue is closed once the verdict is applied.

    Poison-issue guard: each issue is processed inside its own try/except, so
    one persistently-failing issue cannot abort inbox processing for all the
    others. Failures are logged and counted per issue (``failure_counts``,
    defaulting to a module-level map); after ``INBOX_ERROR_THRESHOLD``
    consecutive failures the issue is labelled ``decision:inbox-error`` and a
    single diagnostic comment is posted so it surfaces.

    Returns the list of resolutions taken (for tick metrics + logging).
    """
    if failure_counts is None:
        failure_counts = _inbox_failure_counts
    resolutions: list[InboxResolution] = []
    inbox_errors = 0
    issues = api.list_issues(
        governance_owner, governance_repo, labels=PENDING_LABEL, state="open"
    )
    for issue in issues:
        # The issues endpoint also returns PRs; skip anything that is a PR.
        if "pull_request" in issue:
            continue
        issue_number = issue["number"]
        key = (governance_owner, governance_repo, issue_number)
        try:
            resolution = _resolve_issue(
                api, governance_owner, governance_repo, allowlisted_actors, issue
            )
        except Exception as e:
            inbox_errors += 1
            count = failure_counts.get(key, 0) + 1
            failure_counts[key] = count
            logger.warning(
                "inbox: processing issue %s/%s#%s failed (consecutive "
                "failure %d): %s — continuing with the remaining issues",
                governance_owner, governance_repo, issue_number, count, e,
            )
            if (
                count >= INBOX_ERROR_THRESHOLD
                and INBOX_ERROR_LABEL not in _label_names(issue)
            ):
                _mark_inbox_error(
                    api, governance_owner, governance_repo, issue_number, count, e
                )
            continue
        failure_counts.pop(key, None)
        if resolution is not None:
            resolutions.append(resolution)

    if inbox_errors:
        # Tick-level signal (greppable; main.py's metrics dict is not in scope
        # here, so the count is surfaced via the log).
        logger.warning("inbox: inbox_errors=%d issue(s) failed this tick", inbox_errors)
    return resolutions
