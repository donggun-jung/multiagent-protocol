"""Tests for config/loader.py."""

from __future__ import annotations

from pathlib import Path

from multiagent_protocol.config.loader import load_config


def _write(dir_path: Path, name: str, body: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(body, encoding="utf-8")


def test_load_minimal_config(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml",
           "governance_repo: alice/multiagent-protocol\nsupervised_repos:\n  - alice/repo-a\n")
    _write(cfg_dir, "env.yml", "bot_app_slug: alice-merge-gate\n")

    cfg = load_config(cfg_dir)

    assert cfg.owner.github_login == "alice"
    assert cfg.owner.allowlisted_actors == ("alice",)
    assert cfg.projects.governance_repo == "alice/multiagent-protocol"
    assert cfg.projects.supervised_repos == ("alice/repo-a",)
    assert cfg.env.runner_tier == "actions-free"
    assert cfg.env.classifier_publisher_slug == "github-actions"
    assert cfg.env.bot_app_slug == "alice-merge-gate"
    # Optional configs not present.
    assert cfg.skills.enabled == ()
    assert cfg.agent_registry is None


def test_inbox_defaults_to_governance(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml",
           "governance_repo: alice/protocol\nsupervised_repos: []\n")
    _write(cfg_dir, "env.yml", "bot_app_slug: foo\n")

    cfg = load_config(cfg_dir)
    assert cfg.projects.effective_inbox_repository == "alice/protocol"
    assert cfg.projects.decision_inbox.thresholds.nudge_days == 14
    assert cfg.projects.break_glass.adr_deadline_hours == 24


def test_inbox_override_repository(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml", """\
governance_repo: alice/protocol
supervised_repos: []
decision_inbox:
  repository: alice/inbox
  thresholds:
    nudge_days: 7
    abandon_days: 21
    auto_close_days: 45
""")
    _write(cfg_dir, "env.yml", "bot_app_slug: foo\n")

    cfg = load_config(cfg_dir)
    assert cfg.projects.effective_inbox_repository == "alice/inbox"
    assert cfg.projects.decision_inbox.thresholds.nudge_days == 7
    assert cfg.projects.decision_inbox.thresholds.abandon_days == 21
    assert cfg.projects.decision_inbox.thresholds.auto_close_days == 45


def test_break_glass_deadline_override(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml", """\
governance_repo: alice/protocol
supervised_repos: []
break_glass:
  adr_deadline_hours: 48
""")
    _write(cfg_dir, "env.yml", "bot_app_slug: foo\n")

    cfg = load_config(cfg_dir)
    assert cfg.projects.break_glass.adr_deadline_hours == 48


def test_agent_registry_loaded(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml",
           "governance_repo: alice/protocol\nsupervised_repos: []\n")
    _write(cfg_dir, "env.yml", "bot_app_slug: foo\n")
    _write(cfg_dir, "agent_registry.yml", """\
tools:
  - claude-code
  - codex
  - manual
models:
  claude-code: ["*"]
  codex: ["gpt-5", "gpt-5.5"]
  manual: ["n/a"]
machines:
  - laptop
""")

    cfg = load_config(cfg_dir)
    reg = cfg.agent_registry
    assert reg is not None
    assert reg.tools == ("claude-code", "codex", "manual")
    assert reg.models["codex"] == ("gpt-5", "gpt-5.5")
    assert reg.machines == ("laptop",)
    # Convenience method.
    assert reg.model_allowed("claude-code", "claude-opus-4.7")  # wildcard
    assert reg.model_allowed("codex", "gpt-5")
    assert not reg.model_allowed("codex", "gpt-4")
    assert reg.model_allowed("manual", "n/a")


def test_missing_owner_yml_raises(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "projects.yml",
           "governance_repo: alice/p\nsupervised_repos: []\n")
    _write(cfg_dir, "env.yml", "bot_app_slug: foo\n")
    import pytest
    with pytest.raises(FileNotFoundError):
        load_config(cfg_dir)


# -- R1 / DEC-C: required_checks + audit_only (env default + per-repo override) -
#
# These exercise the loader's PARSING (no schema arg, like the minimal-config
# test above — load_config validates ALL five schemas when given a schemas dir,
# and agent_registry.schema.json rejects an empty {} on its own). Schema
# acceptance of the new keys is covered directly below in test_schemas.

import json as _json  # noqa: E402

import jsonschema as _jsonschema  # noqa: E402

_SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def _load_schema(name: str) -> dict:
    return _json.loads((_SCHEMAS / name).read_text(encoding="utf-8"))


def test_required_checks_and_audit_only_default_empty(tmp_path: Path):
    # Absent → v1.0.0 behavior: no named checks, no audit-only repos.
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml",
           "governance_repo: alice/p\nsupervised_repos:\n  - alice/repo-a\n")
    _write(cfg_dir, "env.yml", "bot_app_slug: foo\n")
    cfg = load_config(cfg_dir)
    assert cfg.env.required_checks == ()
    assert cfg.projects.audit_only_repos == ()
    assert cfg.projects.repo_overrides == {}
    # effective falls through to the (empty) global default.
    assert cfg.projects.effective_required_checks("alice/repo-a", ()) == ()
    assert cfg.projects.is_audit_only("alice/repo-a") is False


def test_required_checks_global_default(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml",
           "governance_repo: alice/p\nsupervised_repos:\n  - alice/repo-a\n")
    _write(cfg_dir, "env.yml",
           "bot_app_slug: foo\nrequired_checks:\n  - lint\n  - test\n")
    cfg = load_config(cfg_dir)
    assert cfg.env.required_checks == ("lint", "test")
    # No per-repo override → repo inherits the global default.
    assert cfg.projects.effective_required_checks(
        "alice/repo-a", cfg.env.required_checks
    ) == ("lint", "test")


def test_required_checks_per_repo_override_wins(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml", """\
governance_repo: alice/p
supervised_repos:
  - alice/repo-a
  - alice/repo-b
repo_overrides:
  alice/repo-a:
    required_checks:
      - build
      - e2e
""")
    _write(cfg_dir, "env.yml",
           "bot_app_slug: foo\nrequired_checks:\n  - lint\n")
    cfg = load_config(cfg_dir)
    g = cfg.env.required_checks
    # repo-a: override wins.
    assert cfg.projects.effective_required_checks("alice/repo-a", g) == ("build", "e2e")
    # repo-b: no override → global default.
    assert cfg.projects.effective_required_checks("alice/repo-b", g) == ("lint",)


def test_required_checks_explicit_empty_override_forces_none(tmp_path: Path):
    # An explicit [] override means "no named checks for this repo" even when a
    # global default is set — distinct from the override being absent.
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml", """\
governance_repo: alice/p
supervised_repos:
  - alice/repo-a
repo_overrides:
  alice/repo-a:
    required_checks: []
""")
    _write(cfg_dir, "env.yml",
           "bot_app_slug: foo\nrequired_checks:\n  - lint\n")
    cfg = load_config(cfg_dir)
    assert cfg.projects.effective_required_checks(
        "alice/repo-a", cfg.env.required_checks
    ) == ()


def test_audit_only_repos_parsed(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml", """\
governance_repo: alice/p
supervised_repos:
  - alice/p
  - alice/repo-a
audit_only_repos:
  - alice/p
""")
    _write(cfg_dir, "env.yml", "bot_app_slug: foo\n")
    cfg = load_config(cfg_dir)
    assert cfg.projects.audit_only_repos == ("alice/p",)
    assert cfg.projects.is_audit_only("alice/p") is True
    assert cfg.projects.is_audit_only("alice/repo-a") is False


def test_env_schema_accepts_required_checks():
    schema = _load_schema("env.schema.json")
    _jsonschema.validate(
        instance={"bot_app_slug": "foo", "required_checks": ["lint", "test"]},
        schema=schema,
    )
    # Still accepts the v1.0.0 shape (field absent).
    _jsonschema.validate(instance={"bot_app_slug": "foo"}, schema=schema)


# -- C2 publisher trust: expected_check_publisher (env default + per-repo) ----

def test_expected_check_publisher_parsed_env_and_override(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml", """\
governance_repo: alice/p
supervised_repos:
  - alice/repo-a
  - alice/repo-b
repo_overrides:
  alice/repo-a:
    expected_check_publisher: custom-ci
""")
    _write(cfg_dir, "env.yml",
           "bot_app_slug: foo\nexpected_check_publisher: org-ci\n")
    cfg = load_config(cfg_dir)
    assert cfg.env.expected_check_publisher == "org-ci"
    assert cfg.projects.repo_overrides["alice/repo-a"].expected_check_publisher == "custom-ci"
    # Resolution: per-repo override > env default.
    g = cfg.env.expected_check_publisher
    assert cfg.projects.effective_expected_check_publisher("alice/repo-a", g) == "custom-ci"
    assert cfg.projects.effective_expected_check_publisher("alice/repo-b", g) == "org-ci"


def test_expected_check_publisher_defaults_unset(tmp_path: Path):
    # Absent everywhere → None; the runtime then uses the built-in default
    # publisher (validator_ci_green.DEFAULT_CHECK_PUBLISHER).
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "owner.yml", "github_login: alice\n")
    _write(cfg_dir, "projects.yml",
           "governance_repo: alice/p\nsupervised_repos:\n  - alice/repo-a\n")
    _write(cfg_dir, "env.yml", "bot_app_slug: foo\n")
    cfg = load_config(cfg_dir)
    assert cfg.env.expected_check_publisher is None
    assert cfg.projects.effective_expected_check_publisher(
        "alice/repo-a", cfg.env.expected_check_publisher) is None


def test_env_schema_accepts_expected_check_publisher():
    schema = _load_schema("env.schema.json")
    _jsonschema.validate(
        instance={"bot_app_slug": "foo", "expected_check_publisher": "org-ci"},
        schema=schema,
    )
    # Still accepts the previous shape (field absent).
    _jsonschema.validate(instance={"bot_app_slug": "foo"}, schema=schema)
    # An empty slug is rejected (minLength 1).
    import pytest
    with pytest.raises(_jsonschema.ValidationError):
        _jsonschema.validate(
            instance={"bot_app_slug": "foo", "expected_check_publisher": ""},
            schema=schema,
        )


def test_projects_schema_accepts_audit_only_and_repo_overrides():
    schema = _load_schema("projects.schema.json")
    _jsonschema.validate(
        instance={
            "governance_repo": "alice/p",
            "supervised_repos": ["alice/p", "alice/repo-a"],
            "audit_only_repos": ["alice/p"],
            "repo_overrides": {"alice/repo-a": {"required_checks": ["build"]}},
        },
        schema=schema,
    )
    # Still accepts the v1.0.0 shape (new fields absent).
    _jsonschema.validate(
        instance={"governance_repo": "alice/p", "supervised_repos": []},
        schema=schema,
    )


def test_projects_schema_rejects_unknown_repo_override_key():
    schema = _load_schema("projects.schema.json")
    import pytest
    with pytest.raises(_jsonschema.ValidationError):
        _jsonschema.validate(
            instance={
                "governance_repo": "alice/p",
                "repo_overrides": {"alice/repo-a": {"bogus_key": 1}},
            },
            schema=schema,
        )
