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
