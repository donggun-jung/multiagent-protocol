"""Decision Inbox resolution loop (decision_inbox.resolve_open_issues).

Security-critical: only allowlisted actors' verdicts count, and a PR whose
head changed after the decision opened is refused (tamper guard).
"""

from __future__ import annotations

from multiagent_protocol.decision_inbox import parse_pr_ref, resolve_open_issues

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
    assert not fake_api.labels_added
    assert not fake_api.merged


def test_approve_b_uses_b_label(fake_api):
    _seed_pending(fake_api, issue_number=9, pr_number=46, head_sha="h" * 40)
    fake_api.seed_comment(9, "owner", "/approve B")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].verdict == "approved-B"
    assert ("example", "repo", 46, "decision:approved-B") in fake_api.labels_added
