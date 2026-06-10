"""Decision Inbox resolution loop (decision_inbox.resolve_open_issues).

Security-critical: only allowlisted actors' verdicts count, and a PR whose
head changed after the decision opened is refused (tamper guard).
"""

from __future__ import annotations

from multiagent_protocol.decision_inbox import (
    INBOX_ERROR_LABEL,
    INBOX_ERROR_THRESHOLD,
    STALE_APPROVAL_LABEL,
    parse_pr_ref,
    resolve_open_issues,
)
from tests.conftest import FakeAPI

ALLOW = ("owner",)


def _seed_pending(fake_api, *, issue_number, pr_number, head_sha):
    body = (
        f"- PR: `example/repo#{pr_number}` — head `{head_sha[:7]}`\n"
        f"<!-- decision-inbox-nonce: abc123 -->\n"
        f"<!-- decision-inbox-head-sha: {head_sha} -->\n"
    )
    fake_api.seed_issue(number=issue_number, labels=("decision:pending-owner",), body=body)
    fake_api.register_pr(owner="example", repo="repo", number=pr_number, head_sha=head_sha)


def test_parse_pr_ref():
    assert parse_pr_ref("- PR: `o/r#42` — head `abc`") == ("o/r", 42)
    assert parse_pr_ref("no reference here") is None


def test_approve_reaction_labels_pr_and_closes_issue(fake_api):
    _seed_pending(fake_api, issue_number=5, pr_number=42, head_sha="h" * 40)
    fake_api.seed_reaction(5, "owner", "+1")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert len(res) == 1 and res[0].action == "labeled" and res[0].verdict == "approved-A"
    assert ("example", "repo", 42, "decision:approved-A") in fake_api.labels_added
    assert ("gov", "repo", 5) in fake_api.closed


def test_reject_comment_closes_pr(fake_api):
    _seed_pending(fake_api, issue_number=6, pr_number=43, head_sha="h" * 40)
    fake_api.seed_comment(6, "owner", "/reject")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].action == "closed-pr"
    assert ("example", "repo", 43) in fake_api.closed  # PR closed
    assert ("gov", "repo", 6) in fake_api.closed       # inbox issue closed


def test_non_allowlisted_actor_ignored(fake_api):
    _seed_pending(fake_api, issue_number=7, pr_number=44, head_sha="h" * 40)
    fake_api.seed_reaction(7, "stranger", "+1")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res == []
    assert not fake_api.labels_added


def test_tamper_when_head_changed(fake_api):
    _seed_pending(fake_api, issue_number=8, pr_number=45, head_sha="x" * 40)
    fake_api._prs[("example", "repo")][0]["head"]["sha"] = "y" * 40  # head moved
    fake_api.seed_reaction(8, "owner", "+1")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].action == "tamper-skip"
    # The PR gets NO approval label; the only label written is the one-time
    # stale-approval marker on the inbox ISSUE itself.
    assert fake_api.labels_added == [("gov", "repo", 8, STALE_APPROVAL_LABEL)]
    assert not fake_api.merged


def test_approve_b_uses_b_label(fake_api):
    _seed_pending(fake_api, issue_number=9, pr_number=46, head_sha="h" * 40)
    fake_api.seed_comment(9, "owner", "/approve B")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].verdict == "approved-B"
    assert ("example", "repo", 46, "decision:approved-B") in fake_api.labels_added


def test_approve_c_defers_does_not_merge_or_close(fake_api):
    # Ballot C = defer (doctrine), NOT a merge.
    _seed_pending(fake_api, issue_number=10, pr_number=47, head_sha="h" * 40)
    fake_api.seed_comment(10, "owner", "/approve C")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].verdict == "deferred" and res[0].action == "deferred"
    assert ("example", "repo", 47, "decision:deferred") in fake_api.labels_added
    assert not any(lbl.startswith("decision:approved") for *_, lbl in fake_api.labels_added)
    assert ("gov", "repo", 10) not in fake_api.closed   # inbox issue stays OPEN
    assert fake_api.merged == []


# -- tamper-comment idempotency (one-time state transition) --------------------


def _mark_issue_labeled(fake_api, issue_number, label):
    """Emulate GitHub state after the bot's add_label call succeeded."""
    issue = next(i for i in fake_api._issues if i["number"] == issue_number)
    issue["labels"].append({"name": label})
    issue["_labels"].add(label)


def test_tamper_comment_posted_once_then_suppressed(fake_api):
    _seed_pending(fake_api, issue_number=11, pr_number=48, head_sha="x" * 40)
    fake_api._prs[("example", "repo")][0]["head"]["sha"] = "y" * 40  # head moved
    fake_api.seed_reaction(11, "owner", "+1")

    # Tick 1: the void-approval comment is posted ONCE + the marker label set.
    res1 = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res1[0].action == "tamper-skip"
    assert len(fake_api.comments_posted) == 1
    assert ("gov", "repo", 11, STALE_APPROVAL_LABEL) in fake_api.labels_added
    _mark_issue_labeled(fake_api, 11, STALE_APPROVAL_LABEL)

    # Tick 2 (and every later tick): marker present → no re-post, no new
    # tamper resolution. The issue stays open awaiting the owner.
    res2 = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res2 == []
    assert len(fake_api.comments_posted) == 1   # NOT re-posted


def test_tamper_marker_does_not_block_verdict_when_head_matches_again(fake_api):
    # If the head returns to the recorded SHA (e.g. the force-push was
    # reverted), the verdict applies normally even with the marker present —
    # the marker only suppresses re-POSTING the tamper comment.
    _seed_pending(fake_api, issue_number=12, pr_number=49, head_sha="x" * 40)
    _mark_issue_labeled(fake_api, 12, STALE_APPROVAL_LABEL)
    fake_api.seed_reaction(12, "owner", "+1")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].action == "labeled" and res[0].verdict == "approved-A"
    assert ("example", "repo", 49, "decision:approved-A") in fake_api.labels_added


# -- poison-issue guard (one failing issue must not block the rest) ------------


class _PoisonReactionsAPI(FakeAPI):
    """FakeAPI whose reactions endpoint persistently fails for ONE issue."""

    def __init__(self, poison_issue: int) -> None:
        super().__init__()
        self._poison = poison_issue

    def list_issue_reactions(self, owner, repo, number):
        if number == self._poison:
            raise RuntimeError("boom: reactions endpoint 500s")
        return super().list_issue_reactions(owner, repo, number)


def test_one_failing_issue_does_not_block_others():
    api = _PoisonReactionsAPI(poison_issue=13)
    _seed_pending(api, issue_number=13, pr_number=50, head_sha="h" * 40)
    _seed_pending(api, issue_number=14, pr_number=51, head_sha="g" * 40)
    api.seed_reaction(14, "owner", "+1")

    res = resolve_open_issues(api, "gov", "repo", ALLOW, failure_counts={})
    # The healthy issue is still resolved despite the earlier poison issue.
    assert [r.issue_number for r in res] == [14]
    assert ("example", "repo", 51, "decision:approved-A") in api.labels_added
    assert ("gov", "repo", 14) in api.closed


def test_repeated_failures_label_inbox_error_once():
    api = _PoisonReactionsAPI(poison_issue=15)
    _seed_pending(api, issue_number=15, pr_number=52, head_sha="h" * 40)
    counts: dict = {}

    for _ in range(INBOX_ERROR_THRESHOLD):
        assert resolve_open_issues(api, "gov", "repo", ALLOW, failure_counts=counts) == []

    # Threshold crossed → labelled + exactly one diagnostic comment.
    assert ("gov", "repo", 15, INBOX_ERROR_LABEL) in api.labels_added
    diags = [c for c in api.comments_posted if c[2] == 15]
    assert len(diags) == 1
    assert "failed to process" in diags[0][3]

    # Once the marker is visible on the issue, later failures do not re-post.
    _mark_issue_labeled(api, 15, INBOX_ERROR_LABEL)
    resolve_open_issues(api, "gov", "repo", ALLOW, failure_counts=counts)
    assert len([c for c in api.comments_posted if c[2] == 15]) == 1
    assert api.labels_added.count(("gov", "repo", 15, INBOX_ERROR_LABEL)) == 1


def test_failure_count_resets_on_success():
    # Two failures, then the endpoint recovers → the consecutive count resets
    # (the issue resolves normally and is never escalated).
    api = _PoisonReactionsAPI(poison_issue=16)
    _seed_pending(api, issue_number=16, pr_number=53, head_sha="h" * 40)
    api.seed_reaction(16, "owner", "+1")
    counts: dict = {}

    for _ in range(INBOX_ERROR_THRESHOLD - 1):
        assert resolve_open_issues(api, "gov", "repo", ALLOW, failure_counts=counts) == []
    assert counts[("gov", "repo", 16)] == INBOX_ERROR_THRESHOLD - 1

    api._poison = -1   # endpoint recovers
    res = resolve_open_issues(api, "gov", "repo", ALLOW, failure_counts=counts)
    assert res[0].action == "labeled"
    assert counts == {}                       # reset on success
    assert ("gov", "repo", 16, INBOX_ERROR_LABEL) not in api.labels_added
