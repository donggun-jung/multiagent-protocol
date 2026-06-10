"""Tests for the fix/vnext-security hardening pass.

End-to-end (FakeAPI + runtime) and unit coverage for:

1. Required-check publisher trust (C2 honours only the expected CI App).
2. SHA-bound approvals (receipts written by the bot; stale receipts void).
3. ``governance/`` as a critical path in the default path classifier.
4. Core L1 validators are non-disableable (runtime + schema).
6. Bot identity fails closed when the authoritative App slug is unavailable.
7. Diagnostic-comment dedupe only trusts the bot's own comments.
8. Squash merges preserve the PR's Agent-* identity trailers.
"""

from __future__ import annotations

import dataclasses

from multiagent_protocol.decision_inbox import resolve_open_issues
from multiagent_protocol.label_provenance import (
    approval_receipt_comment,
    approval_receipts,
)
from multiagent_protocol.runtime import build_runtime_skills, process_pr
from tests.conftest import changed_file, make_check, raw_commit

BOT_USER = "your-merge-gate-bot[bot]"  # solo_config env.bot_app_slug + "[bot]"


def _rt(api, cfg):
    return build_runtime_skills(cfg, api, config_dir=None)


# -- 1. Required-check publisher trust, wired through the runtime -------------

def test_required_check_green_from_foreign_app_blocks_e2e(fake_api, solo_config):
    # env.required_checks=("ci",); the only 'ci' run is green but published by
    # an attacker-controlled App → C2 treats it as not yet green → blocked.
    cfg = dataclasses.replace(
        solo_config, env=dataclasses.replace(solo_config.env, required_checks=("ci",)))
    pr = fake_api.register_pr(
        number=70, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("ci", "success", slug="attacker-app")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "blocked"
    assert fake_api.merged == []


def test_required_check_green_from_expected_app_merges_e2e(fake_api, solo_config):
    # The same PR with the 'ci' run published by github-actions (the default
    # expected publisher) merges.
    cfg = dataclasses.replace(
        solo_config, env=dataclasses.replace(solo_config.env, required_checks=("ci",)))
    pr = fake_api.register_pr(
        number=71, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("ci", "success", slug="github-actions")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "merged"


# -- 2. SHA-bound approvals: receipt write + parse + end-to-end ----------------

SHA1 = "1" * 40
SHA2 = "2" * 40


def test_inbox_approval_writes_sha_receipt(fake_api):
    # When the inbox applies decision:approved-A it must also post a receipt
    # comment on the PR binding the label to the verified head SHA.
    body = (
        "- PR: `example/repo#42` — head `" + "h" * 7 + "`\n"
        "<!-- decision-inbox-nonce: abc123 -->\n"
        "<!-- decision-inbox-head-sha: " + "h" * 40 + " -->\n"
    )
    fake_api.seed_issue(number=5, labels=("decision:pending-owner",), body=body)
    fake_api.register_pr(owner="example", repo="repo", number=42, head_sha="h" * 40)
    fake_api.seed_reaction(5, "owner", "+1")

    resolve_open_issues(fake_api, "gov", "repo", ("owner",))

    assert ("example", "repo", 42, "decision:approved-A") in fake_api.labels_added
    receipts = [b for (o, r, n, b) in fake_api.comments_posted
                if (o, r, n) == ("example", "repo", 42)]
    assert len(receipts) == 1
    # The written receipt round-trips through the parser (as a bot comment).
    parsed = approval_receipts(
        [{"user": {"login": BOT_USER}, "body": receipts[0]}], BOT_USER)
    assert parsed == {"decision:approved-A": "h" * 40}


def test_approval_receipts_ignores_non_bot_authors():
    forged = approval_receipt_comment("decision:approved-A", SHA2)
    assert approval_receipts(
        [{"user": {"login": "mallory"}, "body": forged}], BOT_USER) == {}


def test_approval_receipts_requires_bot_user():
    receipt = approval_receipt_comment("decision:approved-A", SHA2)
    assert approval_receipts(
        [{"user": {"login": BOT_USER}, "body": receipt}], None) == {}


def test_approval_receipts_latest_receipt_wins():
    old = {"user": {"login": BOT_USER},
           "body": approval_receipt_comment("decision:approved-A", SHA1)}
    new = {"user": {"login": BOT_USER},
           "body": approval_receipt_comment("decision:approved-A", SHA2)}
    assert approval_receipts([old, new], BOT_USER) == {"decision:approved-A": SHA2}


def test_approval_receipts_requires_both_markers():
    half = {"user": {"login": BOT_USER},
            "body": "<!-- merge-gate-approval-label: decision:approved-A -->"}
    assert approval_receipts([half], BOT_USER) == {}


# -- 3. governance/ is a critical path -----------------------------------------

def test_governance_scripts_deletion_classifies_d_not_a(pr_factory):
    # governance/ holds the gate's own decision logic; deleting from it is
    # irreversible + critical → Quadrant D (owner), never auto-approved A.
    from multiagent_protocol.skills.builtin.classifier_path_default import (
        PathDefaultClassifier,
    )
    from multiagent_protocol.types import FileChange
    pr = pr_factory(files_changed=(
        FileChange(path="governance/scripts/classify.py", status="removed",
                   additions=0, deletions=50),
    ))
    v = PathDefaultClassifier().evaluate(pr)
    assert v.quadrant == "D"
    assert "governance/" in v.reasoning


def test_governance_rubric_modification_classifies_b(pr_factory):
    # Modifying the gate's reversibility rubric is critical (reversible) → B,
    # i.e. owner-visible audit, not silent Quadrant-A auto-merge.
    from multiagent_protocol.skills.builtin.classifier_path_default import (
        PathDefaultClassifier,
    )
    from multiagent_protocol.types import FileChange
    pr = pr_factory(files_changed=(
        FileChange(path="governance/REVERSIBILITY_RUBRIC.md", status="modified",
                   additions=3, deletions=1),
    ))
    assert PathDefaultClassifier().evaluate(pr).quadrant == "B"


def test_stale_sha_receipt_blocks_merge_after_backdated_commit_e2e(fake_api, solo_config):
    # The approval was recorded at SHA1 (bot receipt). The agent then pushes
    # SHA2 with a committer date BEFORE the approval event (backdated, so the
    # old time-only freshness check would still honour the stale approval).
    # The SHA binding voids it → back to the inbox, no merge.
    pr = fake_api.register_pr(
        number=80, labels=("ready-to-merge", "decision:approved-A"),
        files=[changed_file("src/x.py", status="removed")],  # Quadrant D
        head_sha=SHA2,
        commits=[raw_commit(sha=SHA2, date="2026-05-25T00:00:00Z")],  # backdated
        label_events=[
            {"label": "ready-to-merge", "actor": "your-github-login",
             "created_at": "2026-05-25T00:05:00Z"},
            {"label": "decision:approved-A", "actor": BOT_USER,
             "created_at": "2026-05-25T00:05:00Z"},
        ])
    fake_api.seed_comment(
        80, BOT_USER, approval_receipt_comment("decision:approved-A", SHA1))
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "inbox"
    assert fake_api.merged == []


def test_matching_sha_receipt_merges_e2e(fake_api, solo_config):
    # Re-approved at the current head (receipt binds SHA2 == head) → merges.
    pr = fake_api.register_pr(
        number=81, labels=("ready-to-merge", "decision:approved-A"),
        files=[changed_file("src/x.py", status="removed")],  # Quadrant D
        head_sha=SHA2,
        commits=[raw_commit(sha=SHA2, date="2026-05-25T00:00:00Z")],
        label_events=[
            {"label": "ready-to-merge", "actor": "your-github-login",
             "created_at": "2026-05-25T00:05:00Z"},
            {"label": "decision:approved-A", "actor": BOT_USER,
             "created_at": "2026-05-25T00:05:00Z"},
        ])
    fake_api.seed_comment(
        81, BOT_USER, approval_receipt_comment("decision:approved-A", SHA2))
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "merged"
    assert d.quadrant == "D"
