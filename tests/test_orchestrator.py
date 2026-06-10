"""Orchestrator decisions (runtime.process_pr) + runtime builder + tick no-op.

Exercises the full per-PR decision path against the in-memory FakeAPI defined
in conftest: classify → L1 (minus C3) → C3/owner-approval → merge / inbox /
blocked, plus the config-driven skill toggles and the no-secrets graceful exit.
"""

from __future__ import annotations

import dataclasses

import pytest

from multiagent_protocol.label_provenance import (
    approval_receipt_comment,
    approval_receipts,
)
from multiagent_protocol.main import main
from multiagent_protocol.runtime import build_runtime_skills, process_pr
from tests.conftest import changed_file, make_check, raw_commit

BOT_USER = "your-merge-gate-bot[bot]"  # solo_config env.bot_app_slug + "[bot]"


def _rt(api, cfg):
    return build_runtime_skills(cfg, api, config_dir=None)


def _names(objs):
    return {o.name for o in objs}


# -- process_pr decisions -----------------------------------------------------

def test_quadrant_a_merges(fake_api, solo_config):
    pr = fake_api.register_pr(number=1, labels=("ready-to-merge",),
                              files=[changed_file("README.md")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "merged"
    assert d.quadrant == "A"
    assert fake_api.merged == [("example", "repo", 1, "h" * 40)]


def test_quadrant_b_merges_and_audits(fake_api, solo_config):
    pr = fake_api.register_pr(number=2, labels=("ready-to-merge",),
                              files=[changed_file("src/multiagent_protocol/x.py")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "merged"
    assert d.quadrant == "B"
    assert len(fake_api.merged) == 1
    assert any("decision:auto-approved-critical-reversible" in i["_labels"]
               for i in fake_api.issues_opened)


def test_quadrant_d_opens_inbox_no_merge(fake_api, solo_config):
    pr = fake_api.register_pr(
        number=3, labels=("ready-to-merge",),
        files=[changed_file("src/multiagent_protocol/x.py", status="removed")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "inbox"
    assert d.quadrant == "D"
    assert fake_api.merged == []
    assert any("decision:pending-owner" in i["_labels"] for i in fake_api.issues_opened)


def test_blocked_when_no_ready_label(fake_api, solo_config):
    pr = fake_api.register_pr(number=4, labels=(), files=[changed_file("README.md")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "blocked"
    assert fake_api.merged == []
    assert fake_api.comments_posted  # a diagnostic comment was posted


def test_blocked_when_ci_red(fake_api, solo_config):
    pr = fake_api.register_pr(number=8, labels=("ready-to-merge",),
                              files=[changed_file("README.md")],
                              checks=[make_check("test", "failure")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "blocked"
    assert fake_api.merged == []


def test_no_ci_repo_blocked_by_default(fake_api, solo_config):
    pr = fake_api.register_pr(number=31, labels=("ready-to-merge",),
                              files=[changed_file("README.md")], checks=[])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "blocked"   # fail-closed: no CI signal
    assert fake_api.merged == []


def test_no_ci_repo_merges_when_allow_no_ci(fake_api, solo_config):
    cfg = dataclasses.replace(
        solo_config, env=dataclasses.replace(solo_config.env, allow_no_ci=True))
    pr = fake_api.register_pr(number=30, labels=("ready-to-merge",),
                              files=[changed_file("README.md")], checks=[])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "merged"


# -- observe-only merge kill-switch (MERGE_GATE_MERGE_ENABLED) -----------------
#
# The conftest autouse fixture sets the flag to "true" for the whole suite
# (the behavioral tests above assert real merges); these tests override it
# per-test to pin the observe-only default.

def test_observe_mode_default_withholds_merge(fake_api, solo_config, monkeypatch):
    # Env unset → observe-only (the production default): an otherwise-eligible
    # Quadrant-A PR is NOT merged; the decision records what WOULD have happened.
    monkeypatch.delenv("MERGE_GATE_MERGE_ENABLED", raising=False)
    pr = fake_api.register_pr(number=70, labels=("ready-to-merge",),
                              files=[changed_file("README.md")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "observe"
    assert d.quadrant == "A"
    assert "would have merged as Quadrant A" in d.detail
    assert fake_api.merged == []


def test_observe_mode_false_withholds_merge_and_audit(fake_api, solo_config, monkeypatch):
    # Explicit "false" behaves like unset; a Quadrant-B merge is withheld AND
    # its post-merge passive-audit issue is not opened either.
    monkeypatch.setenv("MERGE_GATE_MERGE_ENABLED", "false")
    pr = fake_api.register_pr(number=71, labels=("ready-to-merge",),
                              files=[changed_file("src/multiagent_protocol/x.py")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "observe"
    assert d.quadrant == "B"
    assert fake_api.merged == []
    assert fake_api.issues_opened == []   # no audit issue without a merge


def test_observe_mode_still_routes_inbox_and_blocks(fake_api, solo_config, monkeypatch):
    # Everything BEFORE the merge runs identically in observe mode: a
    # Quadrant-D PR still opens its Decision Inbox issue, and a label-less PR
    # still blocks with a diagnostic comment.
    monkeypatch.delenv("MERGE_GATE_MERGE_ENABLED", raising=False)
    d_pr = fake_api.register_pr(number=72, labels=("ready-to-merge",),
                                files=[changed_file("src/x.py", status="removed")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), d_pr)
    assert d.action == "inbox"
    assert any("decision:pending-owner" in i["_labels"] for i in fake_api.issues_opened)

    blocked_pr = fake_api.register_pr(number=73, labels=(),
                                      files=[changed_file("README.md")])
    d2 = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), blocked_pr)
    assert d2.action == "blocked"
    assert any(n == 73 for (_o, _r, n, _b) in fake_api.comments_posted)
    assert fake_api.merged == []


@pytest.mark.parametrize("flag", ["true", "TRUE", "True"])
def test_merge_enabled_true_merges_and_audits(fake_api, solo_config, monkeypatch, flag):
    # Flag explicitly true (any case) → exact pre-kill-switch behavior:
    # merge + the Quadrant-B passive-audit issue.
    monkeypatch.setenv("MERGE_GATE_MERGE_ENABLED", flag)
    pr = fake_api.register_pr(number=74, labels=("ready-to-merge",),
                              files=[changed_file("src/multiagent_protocol/x.py")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "merged"
    assert d.quadrant == "B"
    assert len(fake_api.merged) == 1
    assert any("decision:auto-approved-critical-reversible" in i["_labels"]
               for i in fake_api.issues_opened)


# -- R1: required_checks threaded through process_pr (C2) ----------------------

def test_global_required_check_missing_blocks(fake_api, solo_config):
    # env.required_checks=("build",); the PR head only has the default 'ci'
    # check (green) but not 'build' → C2 fails closed → blocked.
    cfg = dataclasses.replace(
        solo_config, env=dataclasses.replace(solo_config.env, required_checks=("build",)))
    pr = fake_api.register_pr(number=50, labels=("ready-to-merge",),
                              files=[changed_file("README.md")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "blocked"
    assert fake_api.merged == []


def test_per_repo_required_check_present_merges(fake_api, solo_config):
    # Per-repo override on example/repo requires 'build'; the head has it green
    # plus an unrelated check → C2 passes → A merges.
    from multiagent_protocol.config.loader import RepoOverride
    cfg = dataclasses.replace(
        solo_config,
        projects=dataclasses.replace(
            solo_config.projects,
            repo_overrides={"example/repo": RepoOverride(required_checks=("build",))},
        ),
    )
    pr = fake_api.register_pr(
        number=51, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("build", "success"), make_check("ci", "success")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "merged"
    assert d.quadrant == "A"


def test_per_repo_required_overrides_global(fake_api, solo_config):
    # Global default requires 'lint'; per-repo override for example/repo requires
    # 'build' instead. A head with 'build' (green) but NO 'lint' must still merge
    # — the per-repo override wins, so 'lint' is not required here.
    from multiagent_protocol.config.loader import RepoOverride
    cfg = dataclasses.replace(
        solo_config,
        env=dataclasses.replace(solo_config.env, required_checks=("lint",)),
        projects=dataclasses.replace(
            solo_config.projects,
            repo_overrides={"example/repo": RepoOverride(required_checks=("build",))},
        ),
    )
    pr = fake_api.register_pr(
        number=52, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("build", "success")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "merged"


# -- C2 publisher trust: expected_check_publisher threaded through process_pr --

def test_per_repo_expected_publisher_override_used(fake_api, solo_config):
    # The repo's CI runs under a non-default App ('custom-ci'). With the
    # per-repo override set, a green required check published by custom-ci
    # satisfies C2 → merges.
    from multiagent_protocol.config.loader import RepoOverride
    cfg = dataclasses.replace(
        solo_config,
        projects=dataclasses.replace(
            solo_config.projects,
            repo_overrides={"example/repo": RepoOverride(
                required_checks=("ci",), expected_check_publisher="custom-ci")},
        ),
    )
    pr = fake_api.register_pr(
        number=54, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("ci", "success", slug="custom-ci")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "merged"


def test_per_repo_expected_publisher_rejects_other_publishers(fake_api, solo_config):
    # Same override; the only green 'ci' run comes from the DEFAULT publisher
    # (github-actions), which is NOT this repo's CI App → fail closed →
    # blocked. Proves the per-PR patch actually swaps the expected publisher
    # (without it, github-actions would have passed).
    from multiagent_protocol.config.loader import RepoOverride
    cfg = dataclasses.replace(
        solo_config,
        projects=dataclasses.replace(
            solo_config.projects,
            repo_overrides={"example/repo": RepoOverride(
                required_checks=("ci",), expected_check_publisher="custom-ci")},
        ),
    )
    pr = fake_api.register_pr(
        number=55, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("ci", "success", slug="github-actions")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "blocked"
    assert "custom-ci" in d.detail
    assert fake_api.merged == []


def test_env_expected_publisher_is_global_default(fake_api, solo_config):
    # env.expected_check_publisher applies to every repo without a per-repo
    # override: green from org-ci merges, green from github-actions does not.
    cfg = dataclasses.replace(
        solo_config,
        env=dataclasses.replace(
            solo_config.env, required_checks=("ci",),
            expected_check_publisher="org-ci"),
    )
    ok = fake_api.register_pr(
        number=56, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("ci", "success", slug="org-ci")])
    rt = _rt(fake_api, cfg)
    assert process_pr(fake_api, cfg, rt, ok).action == "merged"

    not_ours = fake_api.register_pr(
        number=57, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("ci", "success", slug="github-actions")],
        head_sha="x" * 40, commits=[raw_commit(sha="x" * 40)])
    assert process_pr(fake_api, cfg, rt, not_ours).action == "blocked"


def test_quadrant_d_inbox_is_idempotent(fake_api, solo_config):
    pr = fake_api.register_pr(
        number=6, labels=("ready-to-merge",),
        files=[changed_file("src/x.py", status="removed")])
    fake_api.seed_issue(labels=("decision:pending-owner",),
                        body="- PR: `example/repo#6` — head `abc`\n")
    before = len(fake_api.issues_opened)
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "inbox"
    assert len(fake_api.issues_opened) == before  # no duplicate issue


def test_c1_fails_when_label_by_non_allowlisted(fake_api, solo_config):
    pr = fake_api.register_pr(
        number=7, labels=("ready-to-merge",), files=[changed_file("README.md")],
        label_events=[{"label": "ready-to-merge", "actor": "impostor",
                       "created_at": "2026-05-25T00:00:00Z"}])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "blocked"
    assert fake_api.merged == []


def test_quadrant_d_with_owner_approval_label_merges_same_tick(fake_api, solo_config):
    # Owner (your-github-login) hand-applied the approval label. Receipt-
    # required contract with NO one-tick deferral: the bot converts the label
    # into a current-head SHA receipt and honours it the SAME tick (the head
    # cannot change within a tick's execution), so the Quadrant-D PR merges
    # immediately.
    pr = fake_api.register_pr(
        number=9, labels=("ready-to-merge", "decision:approved-A"),
        files=[changed_file("src/x.py", status="removed")])
    rt = _rt(fake_api, solo_config)

    d = process_pr(fake_api, solo_config, rt, pr)
    assert d.action == "merged"
    assert d.quadrant == "D"
    assert len(fake_api.merged) == 1


def test_exploit_a_self_applied_approval_does_not_merge(fake_api, solo_config):
    # A non-allowlisted collaborator self-applies decision:approved-A on a
    # Quadrant-D PR that legitimately has owner-applied ready-to-merge.
    pr = fake_api.register_pr(
        number=20, labels=("ready-to-merge", "decision:approved-A"),
        files=[changed_file("src/x.py", status="removed")],
        label_events=[
            {"label": "ready-to-merge", "actor": "your-github-login", "created_at": "2026-05-25T00:00:00Z"},
            {"label": "decision:approved-A", "actor": "mallory", "created_at": "2026-05-25T00:01:00Z"},
        ])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "inbox"        # routed to owner, NOT merged
    assert fake_api.merged == []


def test_exploit_b_forcepush_with_existing_receipt_does_not_merge(fake_api, solo_config):
    # Exploit B (force-push past an approval) is defeated by the SHA receipt,
    # NOT by committer dates. The bot already observed + receipted the
    # approval at "h"*40 on a prior tick; a force-push then landed a NEW head
    # ("f"*40) with a committer date BACKDATED to 00:00 (before the approval
    # event) so the old time-only guard would have honoured it. C3 voids the
    # stale receipt (recorded "h" != head "f") and the writer never re-binds
    # an approval → routed to the inbox for re-approval. (The old writer-side
    # committer-date check was forgeable and has been removed; the receipt is
    # the real guard.)
    head = "f" * 40
    pr = fake_api.register_pr(
        number=21, labels=("ready-to-merge", "decision:approved-A"),
        files=[changed_file("src/x.py", status="removed")],
        head_sha=head,
        commits=[raw_commit(sha=head, date="2026-05-25T00:00:00Z")],  # backdated
        label_events=[
            {"label": "ready-to-merge", "actor": "your-github-login", "created_at": "2026-05-25T00:11:00Z"},
            {"label": "decision:approved-A", "actor": "your-github-login", "created_at": "2026-05-25T00:11:00Z"},
        ])
    fake_api.seed_comment(
        21, BOT_USER, approval_receipt_comment("decision:approved-A", "h" * 40))
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "inbox"        # stale approval voided by the receipt
    assert fake_api.merged == []
    # The bot wrote NO new approval receipt this tick — it never silently
    # re-binds an approval to the new head (only the inbox may supersede it).
    written = approval_receipts(
        [{"user": {"login": BOT_USER}, "body": b}
         for (*_, b) in fake_api.comments_posted], BOT_USER)
    assert "decision:approved-A" not in written


def test_self_applied_auto_revert_label_does_not_merge_quadrant_d(fake_api, solo_config):
    # A self-applied decision:auto-revert on a Quadrant-D (src delete) PR must
    # NOT merge: the label is unverified (votes A) AND max-vote keeps it D.
    pr = fake_api.register_pr(
        number=42, labels=("ready-to-merge", "decision:auto-revert"),
        files=[changed_file("src/x.py", status="removed")],
        label_events=[
            {"label": "ready-to-merge", "actor": "your-github-login", "created_at": "2026-05-25T00:00:00Z"},
            {"label": "decision:auto-revert", "actor": "mallory", "created_at": "2026-05-25T00:01:00Z"},
        ])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "inbox"
    assert d.quadrant == "D"
    assert fake_api.merged == []


# -- R2: published classifier verdict wired into the runtime ------------------

def test_published_verdict_d_routes_to_inbox(fake_api, solo_config):
    # Path heuristic says A (README), but a canonical-slug classifier-judgment
    # publishes Quadrant: D → max-vote raises to D → routed to the owner inbox,
    # not merged. Proves PublishedVerdictClassifier is in the runtime rule set.
    pr = fake_api.register_pr(
        number=60, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[
            make_check("ci", "success"),
            make_check("classifier-judgment", "neutral", summary="Quadrant: D"),
        ])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.quadrant == "D"
    assert d.action == "inbox"
    assert fake_api.merged == []


def test_published_verdict_c_raises_path_a_and_audits(fake_api, solo_config):
    # Path heuristic says A; canonical judgment publishes Quadrant: C → raised to
    # C, which still auto-approves (C merges) and opens the C audit issue.
    pr = fake_api.register_pr(
        number=61, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[
            make_check("ci", "success"),
            make_check("classifier-judgment", "neutral", summary="Quadrant: C"),
        ])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.quadrant == "C"
    assert d.action == "merged"
    assert any("decision:auto-approved-irreversible-non-critical" in i["_labels"]
               for i in fake_api.issues_opened)


def test_absent_judgment_is_v1_0_behavior(fake_api, solo_config):
    # No classifier-judgment check present → R2 abstains → a path-A PR merges
    # exactly as in v1.0.0 (backward-compat at the orchestrator level).
    pr = fake_api.register_pr(
        number=62, labels=("ready-to-merge",), files=[changed_file("README.md")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.quadrant == "A"
    assert d.action == "merged"


# -- runtime builder: disabled + severity_overrides ---------------------------

def test_disabled_skill_removed(fake_api, solo_config):
    cfg = dataclasses.replace(
        solo_config,
        skills=dataclasses.replace(solo_config.skills, disabled=("classifier_empty_pr",)),
    )
    rt = build_runtime_skills(cfg, fake_api, config_dir=None)
    assert "classifier_empty_pr" not in _names(rt.classifier_rules)


def test_non_disableable_skill_kept(fake_api, solo_config):
    cfg = dataclasses.replace(
        solo_config,
        skills=dataclasses.replace(solo_config.skills, disabled=("validator_trailers",)),
    )
    rt = build_runtime_skills(cfg, fake_api, config_dir=None)
    assert "validator_trailers" in _names(rt.validators)


def test_unauthorized_push_hook_not_disableable(fake_api, solo_config):
    # R3 hardening: hook_unauthorized_push is the only code-level substitute for
    # paid branch protection. It must stay armed even if listed in
    # skills.disabled, so a fleet cannot silently lose main-write monitoring.
    cfg = dataclasses.replace(
        solo_config,
        skills=dataclasses.replace(
            solo_config.skills, disabled=("hook_unauthorized_push",)),
    )
    rt = build_runtime_skills(cfg, fake_api, config_dir=None)
    assert "hook_unauthorized_push" in _names(rt.static_branch_hooks)


def test_break_glass_hook_receives_bot_user(fake_api, solo_config):
    # P2-2 wiring: build_runtime_skills must inject the resolved bot_user into
    # BreakGlassAuditHook (exactly as it does for hook_unauthorized_push), so the
    # hook can short-circuit the bot's own squash of a break-glass-TITLED PR and
    # not raise a false decision:break-glass-unauthorized.
    rt = build_runtime_skills(solo_config, fake_api, config_dir=None)
    bg = next(
        h for h in rt.static_branch_hooks if h.name == "hook_break_glass_audit"
    )
    up = next(
        h for h in rt.static_branch_hooks if h.name == "hook_unauthorized_push"
    )
    assert bg.bot_user == BOT_USER
    assert bg.bot_user == up.bot_user == rt.bot_user  # same identity both hooks


def test_severity_override_applied(fake_api, solo_config):
    cfg = dataclasses.replace(
        solo_config,
        skills=dataclasses.replace(
            solo_config.skills, severity_overrides={"validator_agent_registry": "P0"}),
    )
    rt = build_runtime_skills(cfg, fake_api, config_dir=None)
    ar = next(v for v in rt.validators if v.name == "validator_agent_registry")
    assert ar.severity == "P0"


# -- tick no-op without credentials ------------------------------------------

def test_main_no_secrets_no_config_returns_0(tmp_path, monkeypatch):
    monkeypatch.delenv("MERGE_GATE_APP_ID", raising=False)
    monkeypatch.delenv("MERGE_GATE_PRIVATE_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no config/ — behaves like the framework upstream
    assert main([]) == 0


def test_main_no_secrets_readme_only_config_returns_0(tmp_path, monkeypatch):
    # The framework upstream tracks config/README.md, so config/ always EXISTS
    # in a checkout. That placeholder alone is NOT a deployment — the scheduled
    # tick must still no-op (exit 0), not fail every 5 minutes.
    monkeypatch.delenv("MERGE_GATE_APP_ID", raising=False)
    monkeypatch.delenv("MERGE_GATE_PRIVATE_KEY", raising=False)
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "README.md").write_text("placeholder — your private config goes here\n")
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0


def test_main_no_secrets_with_deployment_config_returns_nonzero(tmp_path, monkeypatch):
    monkeypatch.delenv("MERGE_GATE_APP_ID", raising=False)
    monkeypatch.delenv("MERGE_GATE_PRIVATE_KEY", raising=False)
    cfg = tmp_path / "config"
    cfg.mkdir()
    # a real deployment commits the YAML config files (owner/projects/env)
    (cfg / "owner.yml").write_text("owner_login: someone\n")
    monkeypatch.chdir(tmp_path)
    # deployment config present but no secrets → misconfig → fail loudly.
    assert main([]) == 1


def test_severity_override_cannot_downgrade_core_l1(fake_api, solo_config):
    cfg = dataclasses.replace(
        solo_config,
        skills=dataclasses.replace(
            solo_config.skills,
            severity_overrides={"validator_ready_to_merge": "P3", "validator_ci_green": "P3"}),
    )
    rt = build_runtime_skills(cfg, fake_api, config_dir=None)
    sev = {v.name: v.severity for v in rt.validators}
    assert sev["validator_ready_to_merge"] == "P0"   # downgrade ignored
    assert sev["validator_ci_green"] == "P0"


def test_enabled_allowlist_helper():
    from multiagent_protocol.runtime import _enabled
    allow = frozenset({"user_skill_a"})
    assert _enabled("user_skill_a", frozenset(), allow)       # in allowlist
    assert not _enabled("user_skill_b", frozenset(), allow)   # not in allowlist
    assert _enabled("validator_trailers", frozenset({"validator_trailers"}), allow)  # core always on
    assert _enabled("anything", frozenset())                  # empty allowlist = all
