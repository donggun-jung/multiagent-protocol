"""Orchestrator decisions (runtime.process_pr) + runtime builder + tick no-op.

Exercises the full per-PR decision path against the in-memory FakeAPI defined
in conftest: classify → L1 (minus C3) → C3/owner-approval → merge / inbox /
blocked, plus the config-driven skill toggles and the no-secrets graceful exit.
"""

from __future__ import annotations

import dataclasses

from multiagent_protocol.main import main
from multiagent_protocol.runtime import build_runtime_skills, process_pr
from tests.conftest import changed_file, make_check, raw_commit


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


def test_quadrant_d_with_owner_approval_label_merges(fake_api, solo_config):
    # Owner (your-github-login) applied the approval label, fresh vs head → merges.
    pr = fake_api.register_pr(
        number=9, labels=("ready-to-merge", "decision:approved-A"),
        files=[changed_file("src/x.py", status="removed")])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
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


def test_exploit_b_stale_approval_after_forcepush_does_not_merge(fake_api, solo_config):
    # Owner approved at 00:00; a force-push then landed head at 00:10. The
    # stale approval must not merge unreviewed code.
    pr = fake_api.register_pr(
        number=21, labels=("ready-to-merge", "decision:approved-A"),
        files=[changed_file("src/x.py", status="removed")],
        head_sha="h" * 40,
        commits=[raw_commit(sha="h" * 40, date="2026-05-25T00:10:00Z")],
        label_events=[
            {"label": "ready-to-merge", "actor": "your-github-login", "created_at": "2026-05-25T00:11:00Z"},
            {"label": "decision:approved-A", "actor": "your-github-login", "created_at": "2026-05-25T00:00:00Z"},
        ])
    d = process_pr(fake_api, solo_config, _rt(fake_api, solo_config), pr)
    assert d.action == "inbox"        # stale approval voided
    assert fake_api.merged == []


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
