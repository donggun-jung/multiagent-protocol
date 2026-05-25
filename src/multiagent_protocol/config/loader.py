"""Config loader.

Reads YAML files under ``config/`` and validates against JSON Schema files
under ``schemas/``. Returns typed dataclasses the bot can consume.

The four config files:

- ``config/owner.yml`` — owner identity + allowlisted actors + paths.
- ``config/projects.yml`` — supervised repos + governance repo.
- ``config/env.yml`` — runner tier + bot repo + GitHub App slug.
- ``config/skills.yml`` — enabled/disabled skill names + severity overrides.

Plus the registry: ``config/agent_registry.yml`` — agent tool names,
model identifiers, and machine handles the L4 identity gate trusts.
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


@dataclass(frozen=True)
class ProjectsConfig:
    governance_repo: str            # "<owner>/<repo>"
    supervised_repos: tuple[str, ...]
    bot_repo: str | None = None     # if separate from governance


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
class AppConfig:
    """The full loaded configuration."""

    owner: OwnerConfig
    projects: ProjectsConfig
    env: EnvConfig
    skills: SkillsConfig


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file missing: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _validate(data: dict, schema_path: Path) -> None:
    if not schema_path.exists():
        return  # schema optional during development
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema_validate(instance=data, schema=schema)


def load_config(
    config_dir: Path,
    schemas_dir: Path | None = None,
) -> AppConfig:
    """Read config/*.yml, validate against schemas/, return AppConfig."""
    owner_data = _load_yaml(config_dir / "owner.yml")
    projects_data = _load_yaml(config_dir / "projects.yml")
    env_data = _load_yaml(config_dir / "env.yml")
    skills_data = (config_dir / "skills.yml")
    skills_data = _load_yaml(skills_data) if skills_data.exists() else {}

    if schemas_dir is not None:
        _validate(owner_data, schemas_dir / "owner.schema.json")
        _validate(projects_data, schemas_dir / "projects.schema.json")
        _validate(env_data, schemas_dir / "env.schema.json")
        _validate(skills_data, schemas_dir / "skills.schema.json")

    return AppConfig(
        owner=OwnerConfig(
            github_login=owner_data["github_login"],
            allowlisted_actors=tuple(
                owner_data.get("allowlisted_actors", [owner_data["github_login"]])
            ),
        ),
        projects=ProjectsConfig(
            governance_repo=projects_data["governance_repo"],
            supervised_repos=tuple(projects_data.get("supervised_repos", [])),
            bot_repo=projects_data.get("bot_repo"),
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
    )
