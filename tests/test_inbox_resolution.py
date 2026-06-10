"""Decision Inbox resolution loop (decision_inbox.resolve_open_issues).

Security-critical: only allowlisted actors' verdicts count, and a PR whose
head changed after the decision opened is refused (tamper guard).
"""

from __future__ import annotations

from multiagent_protocol.decision_inbox import (
    INBOX_ERROR_LABEL,
    INBOX_ERROR_THRESHOLD,
    INBOX_INTEGRITY_LABEL,
    STALE_APPROVAL_LABEL,
    issue_body,
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


# -- A6: Decision Inbox body integrity (MERGE_GATE_RECEIPT_KEY set) ------------
#
# The resolver reads the authoritative PR ref + head SHA from the (mutable)
# issue body. With the key set, the body carries a MAC over (pr_full_name,
# pr_number, head_sha, nonce); a verdict is honoured ONLY if the body verifies.
# An edit that redirects the PR ref/head fails the MAC and is refused. The
# unset-key fallback resolves as before.

KEY = "go-live-key"


def _seed_signed_pending(fake_api, *, issue_number, pr_number, head_sha,
                         pr_full_name="example/repo"):
    """Seed a pending inbox issue whose body is correctly MAC-signed.

    The env key must already be set (via monkeypatch) when this is called so
    issue_body() embeds a valid marker. Returns the signed body string.
    """
    body = issue_body(
        pr_full_name=pr_full_name, pr_number=pr_number, head_sha=head_sha,
        classifier_reasoning="r", nonce="nonce-" + str(issue_number),
    )
    fake_api.seed_issue(number=issue_number, labels=("decision:pending-owner",),
                        body=body)
    owner, _, repo = pr_full_name.partition("/")
    fake_api.register_pr(owner=owner, repo=repo, number=pr_number, head_sha=head_sha)
    return body


def _replace_issue_body(fake_api, issue_number, new_body):
    issue = next(i for i in fake_api._issues if i["number"] == issue_number)
    issue["body"] = new_body


def test_signed_body_valid_mac_resolves(fake_api, monkeypatch):
    # Control: a correctly-signed body resolves the owner verdict normally.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", KEY)
    _seed_signed_pending(fake_api, issue_number=20, pr_number=60, head_sha="h" * 40)
    fake_api.seed_reaction(20, "owner", "+1")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].action == "labeled" and res[0].verdict == "approved-A"
    assert ("example", "repo", 60, "decision:approved-A") in fake_api.labels_added


def test_signed_body_pr_ref_tamper_refused(fake_api, monkeypatch):
    # A6 redirect: the body is signed for example/repo#61, then an editor
    # rewrites the PR ref to evil/repo#61 to redirect the owner's approval.
    # The MAC fails → verdict NOT honoured; integrity comment + marker posted;
    # NO approval label on any PR.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", KEY)
    _seed_signed_pending(fake_api, issue_number=21, pr_number=61, head_sha="h" * 40)
    fake_api.seed_reaction(21, "owner", "+1")
    issue = next(i for i in fake_api._issues if i["number"] == 21)
    _replace_issue_body(fake_api, 21, issue["body"].replace(
        "example/repo#61", "evil/repo#61"))
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].action == "integrity-skip"
    assert res[0].verdict == "integrity-failed"
    assert not any(lbl.startswith("decision:approved")
                   for *_, lbl in fake_api.labels_added)
    assert ("gov", "repo", 21, INBOX_INTEGRITY_LABEL) in fake_api.labels_added
    integ = [c for c in fake_api.comments_posted if c[2] == 21]
    assert len(integ) == 1 and "integrity check failed" in integ[0][3]


def test_signed_body_head_sha_tamper_refused(fake_api, monkeypatch):
    # Rewriting the recorded head SHA in the body (to point the approval at a
    # different, unreviewed head) also fails the MAC → refused.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", KEY)
    _seed_signed_pending(fake_api, issue_number=22, pr_number=62, head_sha="a" * 40)
    fake_api.seed_reaction(22, "owner", "+1")
    issue = next(i for i in fake_api._issues if i["number"] == 22)
    # Change BOTH the short and full head SHA in the body to a different value.
    tampered = issue["body"].replace("a" * 40, "b" * 40).replace("a" * 7, "b" * 7)
    _replace_issue_body(fake_api, 22, tampered)
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].action == "integrity-skip"
    assert not fake_api.merged
    assert not any(lbl.startswith("decision:approved")
                   for *_, lbl in fake_api.labels_added)


def test_signed_body_missing_mac_refused(fake_api, monkeypatch):
    # A body with the PR ref + head SHA but NO MAC marker (e.g. an attacker
    # opened the issue by hand, or stripped the marker) is refused under a set
    # key — fail closed.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", KEY)
    body = (
        "- PR: `example/repo#63` — head `" + "h" * 7 + "`\n"
        "<!-- decision-inbox-nonce: n -->\n"
        "<!-- decision-inbox-head-sha: " + "h" * 40 + " -->\n"
    )  # no merge-gate-mac marker
    fake_api.seed_issue(number=23, labels=("decision:pending-owner",), body=body)
    fake_api.register_pr(owner="example", repo="repo", number=63, head_sha="h" * 40)
    fake_api.seed_reaction(23, "owner", "+1")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].action == "integrity-skip"
    assert not any(lbl.startswith("decision:approved")
                   for *_, lbl in fake_api.labels_added)


def test_integrity_comment_posted_once_then_suppressed(fake_api, monkeypatch):
    # Like the head-SHA tamper path: the integrity comment is posted ONCE, then
    # the marker label suppresses re-posting on every later tick.
    monkeypatch.setenv("MERGE_GATE_RECEIPT_KEY", KEY)
    _seed_signed_pending(fake_api, issue_number=24, pr_number=64, head_sha="h" * 40)
    fake_api.seed_reaction(24, "owner", "+1")
    issue = next(i for i in fake_api._issues if i["number"] == 24)
    _replace_issue_body(fake_api, 24, issue["body"].replace(
        "example/repo#64", "evil/repo#64"))

    res1 = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res1[0].action == "integrity-skip"
    assert len([c for c in fake_api.comments_posted if c[2] == 24]) == 1
    # Emulate GitHub state after the marker label was added.
    issue["labels"].append({"name": INBOX_INTEGRITY_LABEL})
    issue["_labels"].add(INBOX_INTEGRITY_LABEL)

    res2 = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res2 == []
    assert len([c for c in fake_api.comments_posted if c[2] == 24]) == 1  # not re-posted


def test_unset_key_unsigned_body_resolves_fallback(fake_api, monkeypatch):
    # Fallback: with the key UNSET, an UNSIGNED body resolves exactly as before
    # (the prior behaviour the whole suite already pins) — the hardening does
    # not break unsigned deployments.
    monkeypatch.delenv("MERGE_GATE_RECEIPT_KEY", raising=False)
    _seed_pending(fake_api, issue_number=25, pr_number=65, head_sha="h" * 40)
    fake_api.seed_reaction(25, "owner", "+1")
    res = resolve_open_issues(fake_api, "gov", "repo", ALLOW)
    assert res[0].action == "labeled" and res[0].verdict == "approved-A"
    assert ("example", "repo", 65, "decision:approved-A") in fake_api.labels_added
