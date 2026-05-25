"""Config loader.

Reads YAML files under ``config/`` and validates against JSON Schema files
under ``schemas/``. Returns typed dataclasses the bot can consume.

The five config files:

- ``config/owner.yml`` — owner identity + allowlisted actors.
- ``config/projects.yml`` — governance/supervised/bot repos + Decision Inbox +
  break-glass deadline overrides.
- ``config/env.yml`` — runner tier + bot App slug + classifier publisher.
- ``config/skills.yml`` — enabled/disabled skill names + severity overrides.
- ``config/agent_registry.yml`` — agent tool names, model identifiers, and
  machine handles that the L4 identity gate trusts.

Missing optional config files are tolerated (treated as empty); missing
``owner.yml`` or ``projects.yml`` is a hard error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import validate as jsonschema_validate


@dataclass(frozen=True)
class OwnerConfig:
    github_login: str
    allowlisted_actors: tuple[str, ...]
    display_name: str | None = None


@dataclass(frozen=True)
class InboxThresholds:
    nudge_days: int = 14
    abandon_days: int = 30
    auto_close_days: int = 60


@dataclass(frozen=True)
class DecisionInboxConfig:
    repository: str | None = None  # falls back to projects.governance_repo
    thresholds: InboxThresholds = field(default_factory=InboxThresholds)


@dataclass(frozen=True)
class BreakGlassConfig:
    adr_deadline_hours: int = 24


@dataclass(frozen=True)
class ProjectsConfig:
    governance_repo: str            # "<owner>/<repo>"
    supervised_repos: tuple[str, ...]
    bot_repo: str | None = None     # if separate from governance
    decision_inbox: DecisionInboxConfig = field(default_factory=DecisionInboxConfig)
    break_glass: BreakGlassConfig = field(default_factory=BreakGlassConfig)

    @property
    def effective_inbox_repository(self) -> str:
        """Return the Decision Inbox host: explicit override or governance_repo."""
        return self.decision_inbox.repository or self.governance_repo

    @property
    def effective_bot_repo(self) -> str:
        return self.bot_repo or self.governance_repo


@dataclass(frozen=True)
class EnvConfig:
    runner_tier: str                # "actions-free" | "self-hosted" | "paid-cloud"
    classifier_publisher_slug: str  # default "github-actions"
    bot_app_slug: str               # GitHub App slug, e.g. "my-bot"


@dataclass(frozen=True)
class SkillsConfig:
    enabled: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    severity_overrides: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRegistry:
    """Permitted Agent-* trailer values for the L4 identity gate.

    See ``schemas/agent_registry.schema.json`` and
    ``docs/concepts/four-quadrants.md`` § "L4 burn-in".
    """

    tools: tuple[str, ...]
    models: dict[str, tuple[str, ...]]   # tool -> accepted models (or ("*",))
    machines: tuple[str, ...] = ()

    def model_allowed(self, tool: str, model: str | None) -> bool:
        """Return True if (tool, model) is in the registry (or wildcard '*')."""
        allowed = self.models.get(tool)
        if allowed is None:
            # Tool not declared in models map → accept any model (defensive).
            return tool in self.tools
        if "*" in allowed:
            return True
        return model in allowed


@dataclass(frozen=True)
class AppConfig:
    """The full loaded configuration."""

    owner: OwnerConfig
    projects: ProjectsConfig
    env: EnvConfig
    skills: SkillsConfig
    agent_registry: AgentRegistry | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file missing: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_yaml_optional(path: Path) -> dict[str, Any]:
    """Same as ``_load_yaml`` but returns {} when the file is absent."""
    if not path.exists():
        return {}
    return _load_yaml(path)


def _validate(data: dict, schema_path: Path) -> None:
    if not schema_path.exists():
        return  # schema optional during development
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema_validate(instance=data, schema=schema)


def _build_decision_inbox(raw: dict) -> DecisionInboxConfig:
    if not raw:
        return DecisionInboxConfig()
    thresholds_raw = raw.get("thresholds") or {}
    return DecisionInboxConfig(
        repository=raw.get("repository"),
        thresholds=InboxThresholds(
            nudge_days=int(thresholds_raw.get("nudge_days", 14)),
            abandon_days=int(thresholds_raw.get("abandon_days", 30)),
            auto_close_days=int(thresholds_raw.get("auto_close_days", 60)),
        ),
    )


def _build_break_glass(raw: dict) -> BreakGlassConfig:
    if not raw:
        return BreakGlassConfig()
    return BreakGlassConfig(
        adr_deadline_hours=int(raw.get("adr_deadline_hours", 24)),
    )


def _build_agent_registry(raw: dict) -> AgentRegistry | None:
    if not raw:
        return None
    tools = tuple(raw.get("tools") or ())
    models_raw = raw.get("models") or {}
    models = {
        tool: tuple(models_raw.get(tool, ("*",))) for tool in tools
    }
    machines = tuple(raw.get("machines") or ())
    return AgentRegistry(tools=tools, models=models, machines=machines)


def load_config(
    config_dir: Path,
    schemas_dir: Path | None = None,
) -> AppConfig:
    """Read config/*.yml, validate against schemas/, return AppConfig.

    ``owner.yml``, ``projects.yml``, ``env.yml`` are required. ``skills.yml``
    and ``agent_registry.yml`` are optional (treated as empty if absent).
    """
    owner_data = _load_yaml(config_dir / "owner.yml")
    projects_data = _load_yaml(config_dir / "projects.yml")
    env_data = _load_yaml(config_dir / "env.yml")
    skills_data = _load_yaml_optional(config_dir / "skills.yml")
    agent_registry_data = _load_yaml_optional(config_dir / "agent_registry.yml")

    if schemas_dir is not None:
        _validate(owner_data, schemas_dir / "owner.schema.json")
        _validate(projects_data, schemas_dir / "projects.schema.json")
        _validate(env_data, schemas_dir / "env.schema.json")
        _validate(skills_data, schemas_dir / "skills.schema.json")
        _validate(agent_registry_data, schemas_dir / "agent_registry.schema.json")

    return AppConfig(
        owner=OwnerConfig(
            github_login=owner_data["github_login"],
            allowlisted_actors=tuple(
                owner_data.get("allowlisted_actors", [owner_data["github_login"]])
            ),
            display_name=owner_data.get("display_name"),
        ),
        projects=ProjectsConfig(
            governance_repo=projects_data["governance_repo"],
            supervised_repos=tuple(projects_data.get("supervised_repos", [])),
            bot_repo=projects_data.get("bot_repo"),
            decision_inbox=_build_decision_inbox(projects_data.get("decision_inbox") or {}),
            break_glass=_build_break_glass(projects_data.get("break_glass") or {}),
        ),
        env=EnvConfig(
            runner_tier=env_data.get("runner_tier", "actions-free"),
            classifier_publisher_slug=env_data.get(
                "classifier_publisher_slug", "github-actions"
            ),
            bot_app_slug=env_data["bot_app_slug"],
        ),
        skills=SkillsConfig(
            enabled=tuple(skills_data.get("enabled", [])),
            disabled=tuple(skills_data.get("disabled", [])),
            severity_overrides=dict(skills_data.get("severity_overrides", {})),
        ),
        agent_registry=_build_agent_registry(agent_registry_data),
    )
