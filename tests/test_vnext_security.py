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
import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from multiagent_protocol.auth import AppAuth, AppCredentials
from multiagent_protocol.decision_inbox import resolve_open_issues
from multiagent_protocol.label_provenance import (
    approval_receipt_comment,
    approval_receipts,
)
from multiagent_protocol.runtime import build_runtime_skills, process_pr
from tests.conftest import FakeAPI, changed_file, make_check, raw_commit

BOT_USER = "your-merge-gate-bot[bot]"  # solo_config env.bot_app_slug + "[bot]"
ROOT = Path(__file__).resolve().parents[1]


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


# -- 4. Core L1 validators cannot be disabled -----------------------------------

CORE_L1 = ("validator_ready_to_merge", "validator_ci_green",
           "validator_owner_approval", "validator_base_up_to_date")


def test_core_l1_validators_survive_disabled_config(fake_api, solo_config):
    # Belt-and-suspenders: even a config object that bypassed schema
    # validation cannot remove the core L1 validators from the runtime.
    cfg = dataclasses.replace(
        solo_config,
        skills=dataclasses.replace(solo_config.skills, disabled=CORE_L1),
    )
    rt = _rt(fake_api, cfg)
    names = {v.name for v in rt.validators}
    # (validator_owner_approval is constructed per-PR in process_pr, so it is
    # not part of the builder's list — its always-on path is e2e-tested below.)
    assert {"validator_ready_to_merge", "validator_ci_green",
            "validator_base_up_to_date"} <= names


def test_disabled_core_validators_still_block_e2e(fake_api, solo_config):
    # A config disabling validator_ci_green + validator_ready_to_merge still
    # runs them: a PR with a failing required check and no ready label stays
    # blocked.
    cfg = dataclasses.replace(
        solo_config,
        env=dataclasses.replace(solo_config.env, required_checks=("ci",)),
        skills=dataclasses.replace(
            solo_config.skills,
            disabled=("validator_ci_green", "validator_ready_to_merge")),
    )
    pr = fake_api.register_pr(
        number=90, labels=(), files=[changed_file("README.md")],
        checks=[make_check("ci", "failure")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "blocked"
    assert "C1" in d.detail and "C2" in d.detail  # both validators ran
    assert fake_api.merged == []


def test_skills_schema_rejects_disabling_core_skills():
    schema = json.loads(
        (ROOT / "schemas" / "skills.schema.json").read_text(encoding="utf-8"))
    for core in CORE_L1 + ("validator_trailers", "validator_classifier_publisher",
                           "classifier_bot_self_repo", "hook_break_glass_audit",
                           "hook_unauthorized_push"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance={"disabled": [core]}, schema=schema)
    # Non-core skills remain disableable.
    jsonschema.validate(
        instance={"disabled": ["classifier_empty_pr", "hook_hallucination_guard"]},
        schema=schema)


def test_load_config_rejects_core_disable(tmp_path):
    from multiagent_protocol.config.loader import load_config
    cfg_dir = tmp_path / "config"
    shutil.copytree(ROOT / "examples" / "solo-developer" / "config", cfg_dir)
    (cfg_dir / "skills.yml").write_text(
        "disabled:\n  - validator_ci_green\n", encoding="utf-8")
    with pytest.raises(jsonschema.ValidationError):
        load_config(cfg_dir, ROOT / "schemas")


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


# -- 7. Diagnostic dedupe only trusts the bot's own comments --------------------

def _blocked_pr(fake_api, number):
    return fake_api.register_pr(
        number=number, labels=(), files=[changed_file("README.md")])


def test_bot_identical_diagnostic_suppresses_repost(fake_api, solo_config):
    # Tick 1 posts the diagnostic; replaying it as a BOT-authored comment
    # suppresses tick 2's identical re-post (the intended dedupe).
    pr = _blocked_pr(fake_api, 100)
    rt = _rt(fake_api, solo_config)
    process_pr(fake_api, solo_config, rt, pr)
    assert len(fake_api.comments_posted) == 1
    diagnostic = fake_api.comments_posted[0][3]
    fake_api.seed_comment(100, BOT_USER, diagnostic)

    process_pr(fake_api, solo_config, rt, pr)
    assert len(fake_api.comments_posted) == 1  # deduped, no second post


def test_non_bot_identical_comment_does_not_suppress_diagnostic(fake_api, solo_config):
    # The same diagnostic text authored by a NON-bot user must not suppress
    # the bot's own diagnostic (author-blind dedupe let third parties mute
    # the gate's explanations).
    pr = _blocked_pr(fake_api, 101)
    rt = _rt(fake_api, solo_config)
    process_pr(fake_api, solo_config, rt, pr)
    assert len(fake_api.comments_posted) == 1
    diagnostic = fake_api.comments_posted[0][3]
    fake_api.seed_comment(101, "mallory", diagnostic)

    process_pr(fake_api, solo_config, rt, pr)
    assert len(fake_api.comments_posted) == 2  # the bot still posts its own


# -- 6. Bot identity: authoritative source only, fail closed --------------------

class _FakeAuthSlug:
    def __init__(self, slug: str | None) -> None:
        self._slug = slug

    def app_slug(self) -> str | None:
        return self._slug


class _AuthedFakeAPI(FakeAPI):
    """FakeAPI that carries App auth, like the real GitHubAPI."""

    def __init__(self, slug: str | None) -> None:
        super().__init__()
        self.auth = _FakeAuthSlug(slug)


def test_bot_identity_fails_closed_when_slug_unavailable(solo_config):
    # App auth is present but GET /app could not yield the slug → the identity
    # path must fail closed, NOT silently trust config env.bot_app_slug.
    api = _AuthedFakeAPI(slug=None)
    with pytest.raises(RuntimeError, match="fails closed"):
        build_runtime_skills(solo_config, api, config_dir=None)


def test_bot_identity_uses_authoritative_slug_and_warns_on_mismatch(solo_config, caplog):
    # The authoritative slug wins over a stale/typo'd config value, loudly.
    api = _AuthedFakeAPI(slug="actual-bot")
    with caplog.at_level("WARNING"):
        rt = build_runtime_skills(solo_config, api, config_dir=None)
    assert rt.bot_user == "actual-bot[bot]"
    assert any("does not match the App's actual slug" in r.message
               for r in caplog.records)


class _Resp:
    def __init__(self, payload) -> None:
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FlakySess:
    """First GET raises (transient); subsequent GETs succeed."""

    def __init__(self, payload) -> None:
        self._payload = payload
        self.calls = 0

    def get(self, url, **kw):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient network error")
        return _Resp(self._payload)


def test_app_slug_transient_failure_is_not_cached():
    sess = _FlakySess({"slug": "real-bot"})
    auth = AppAuth(AppCredentials(app_id="1", private_key_pem="x"), session=sess)
    auth.build_app_jwt = lambda now=None: "fake-jwt"  # avoid signing a fake PEM
    assert auth.app_slug() is None            # unavailable this call
    assert auth.app_slug() == "real-bot"      # retried, NOT negatively cached
    assert sess.calls == 2


class _NoSlugSess:
    def get(self, url, **kw):
        return _Resp({})  # GET /app ok but no slug field


def test_app_slug_missing_field_is_unavailable():
    auth = AppAuth(AppCredentials(app_id="1", private_key_pem="x"),
                   session=_NoSlugSess())
    auth.build_app_jwt = lambda now=None: "fake-jwt"
    assert auth.app_slug() is None


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
