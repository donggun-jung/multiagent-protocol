"""Config loader.

Reads YAML files under ``config/`` and validates against JSON Schema files
under ``schemas/``. Returns typed dataclasses the bot can consume.

The five config files:

- ``config/owner.yml`` — owner identity + allowlisted actors.
- ``config/projects.yml`` — governance/supervised/bot repos + Decision Inbox +
  break-glass deadline overrides.
- ``config/env.yml`` — runner tier + bot App slug + classifier publisher.
- ``config/skills.yml`` — enabled/disabled skill names + severity overrides.
- ``config/agent_registry.yml`` — agent tool names + model identifiers that
  the L4 identity gate trusts.

Missing optional config files are tolerated (treated as empty); missing
``owner.yml`` or ``projects.yml`` is a hard error.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import validate as jsonschema_validate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerConfig:
    github_login: str
    allowlisted_actors: tuple[str, ...]


@dataclass(frozen=True)
class DecisionInboxConfig:
    repository: str | None = None  # falls back to projects.governance_repo


@dataclass(frozen=True)
class BreakGlassConfig:
    adr_deadline_hours: int = 24


@dataclass(frozen=True)
class RepoOverride:
    """Per-repo overrides keyed by ``owner/name`` in ``projects.repo_overrides``.

    - ``required_checks`` (R1): the named CI checks that MUST be present +
      green on a PR head for C2/L2 to pass in this repo. ``None`` means "no
      per-repo override" → fall back to the global ``env.required_checks``.
    - ``expected_check_publisher``: the GitHub App slug that must have
      published a required check for it to count as green in this repo (C2
      publisher trust). ``None`` → fall back to the global
      ``env.expected_check_publisher``, then to the built-in default
      (``github-actions``).
    """

    required_checks: tuple[str, ...] | None = None
    expected_check_publisher: str | None = None


@dataclass(frozen=True)
class ProjectsConfig:
    governance_repo: str            # "<owner>/<repo>"
    supervised_repos: tuple[str, ...]
    bot_repo: str | None = None     # if separate from governance
    decision_inbox: DecisionInboxConfig = field(default_factory=DecisionInboxConfig)
    break_glass: BreakGlassConfig = field(default_factory=BreakGlassConfig)
    # DEC-C: repos audited (L2/L5) but NOT PR-gated (L1–L4). Empty = every
    # supervised repo is gated (v1.0.0 behavior).
    audit_only_repos: tuple[str, ...] = ()
    # R1: per-repo ``required_checks`` override, keyed by ``owner/name``.
    repo_overrides: dict[str, RepoOverride] = field(default_factory=dict)

    @property
    def effective_inbox_repository(self) -> str:
        """Return the Decision Inbox host: explicit override or governance_repo."""
        return self.decision_inbox.repository or self.governance_repo

    @property
    def effective_bot_repo(self) -> str:
        return self.bot_repo or self.governance_repo

    def is_audit_only(self, full_name: str) -> bool:
        """True iff this repo is audit-only (DEC-C): scanned but not PR-gated."""
        return full_name in self.audit_only_repos

    def effective_required_checks(
        self, full_name: str, global_default: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        """R1: per-repo override wins; else the global default; else ()."""
        override = self.repo_overrides.get(full_name)
        if override is not None and override.required_checks is not None:
            return override.required_checks
        return global_default

    def effective_expected_check_publisher(
        self, full_name: str, global_default: str | None = None
    ) -> str | None:
        """C2 publisher trust: per-repo override > env default > None.

        ``None`` tells the caller to use the built-in default publisher
        (``validator_ci_green.DEFAULT_CHECK_PUBLISHER``).
        """
        override = self.repo_overrides.get(full_name)
        if override is not None and override.expected_check_publisher is not None:
            return override.expected_check_publisher
        return global_default


@dataclass(frozen=True)
class EnvConfig:
    runner_tier: str                # "actions-free" | "self-hosted" | "paid-cloud"
    classifier_publisher_slug: str  # default "github-actions"
    bot_app_slug: str               # GitHub App slug, e.g. "my-bot"
    allow_no_ci: bool = False       # if True, a head with zero checks passes C2
    # R1: global default named CI checks that MUST be present + green for C2/L2.
    # Empty = today's behavior (all completed checks must succeed). A per-repo
    # ``projects.repo_overrides[<repo>].required_checks`` overrides this.
    required_checks: tuple[str, ...] = ()
    # C2 publisher trust: the GitHub App slug that must have published a
    # required check for it to count as green. Unset (None) → the built-in
    # default ``github-actions``. A per-repo
    # ``projects.repo_overrides[<repo>].expected_check_publisher`` overrides this.
    expected_check_publisher: str | None = None
    # FEATURE A — L2 auto-revert PR (opt-in, default OFF). When True, an L2
    # real-failure ALSO opens a revert PR in the supervised repo (the revert
    # still passes through the normal gate; it is never auto-labelled
    # ready-to-merge). Default False = detection + incident only.
    auto_revert_pr: bool = False
    # FEATURE B — L4 60-day burn-in auto-promotion (opt-in, default 0 = OFF).
    # A positive int is the number of days after which ``validator_agent_registry``
    # is promoted from advisory (P2) to hard-block (P0) automatically, unless the
    # operator pinned its severity via ``skills.severity_overrides``.
    l4_burn_in_days: int = 0


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
    """Parse ``projects.decision_inbox``. Only ``repository`` is read; the
    legacy ``thresholds`` keys (nudge/abandon/auto_close days) configured an
    inbox lifecycle that was never implemented and are ignored if present."""
    if not raw:
        return DecisionInboxConfig()
    return DecisionInboxConfig(repository=raw.get("repository"))


def _build_break_glass(raw: dict) -> BreakGlassConfig:
    if not raw:
        return BreakGlassConfig()
    return BreakGlassConfig(
        adr_deadline_hours=int(raw.get("adr_deadline_hours", 24)),
    )


def _build_repo_overrides(raw: dict) -> dict[str, RepoOverride]:
    """Parse ``projects.repo_overrides`` into a ``{full_name: RepoOverride}`` map.

    Absent / empty → ``{}`` (no per-repo overrides; R1 falls back to the global
    ``env.required_checks``). A ``required_checks`` key absent in an entry stays
    ``None`` (distinct from an explicit empty list, which forces "no named
    checks" for that repo).
    """
    if not raw:
        return {}
    out: dict[str, RepoOverride] = {}
    for full_name, entry in raw.items():
        entry = entry or {}
        rc = entry.get("required_checks")
        out[full_name] = RepoOverride(
            required_checks=tuple(rc) if rc is not None else None,
            expected_check_publisher=entry.get("expected_check_publisher"),
        )
    return out


def _build_agent_registry(raw: dict) -> AgentRegistry | None:
    """Parse ``agent_registry.yml``. The legacy ``machines`` key is ignored if
    present — it was loaded but never consumed (the L4 gate deliberately does
    not hard-block unknown machine handles)."""
    if not raw:
        return None
    tools = tuple(raw.get("tools") or ())
    models_raw = raw.get("models") or {}
    models = {
        tool: tuple(models_raw.get(tool, ("*",))) for tool in tools
    }
    return AgentRegistry(tools=tools, models=models)


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

    config = AppConfig(
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
            decision_inbox=_build_decision_inbox(projects_data.get("decision_inbox") or {}),
            break_glass=_build_break_glass(projects_data.get("break_glass") or {}),
            audit_only_repos=tuple(projects_data.get("audit_only_repos", [])),
            repo_overrides=_build_repo_overrides(projects_data.get("repo_overrides") or {}),
        ),
        env=EnvConfig(
            runner_tier=env_data.get("runner_tier", "actions-free"),
            classifier_publisher_slug=env_data.get(
                "classifier_publisher_slug", "github-actions"
            ),
            bot_app_slug=env_data["bot_app_slug"],
            allow_no_ci=bool(env_data.get("allow_no_ci", False)),
            required_checks=tuple(env_data.get("required_checks", [])),
            expected_check_publisher=env_data.get("expected_check_publisher"),
            auto_revert_pr=bool(env_data.get("auto_revert_pr", False)),
            l4_burn_in_days=int(env_data.get("l4_burn_in_days", 0)),
        ),
        skills=SkillsConfig(
            enabled=tuple(skills_data.get("enabled", [])),
            disabled=tuple(skills_data.get("disabled", [])),
            severity_overrides=dict(skills_data.get("severity_overrides", {})),
        ),
        agent_registry=_build_agent_registry(agent_registry_data),
    )
    _require_explicit_ci_posture(config)
    return config


def _require_explicit_ci_posture(config: AppConfig) -> None:
    """A2: warn loudly when a gated repo has no explicit CI posture.

    A GATED (non-audit-only) supervised repo should declare an explicit CI
    posture: either named ``required_checks`` (per-repo override or the env
    default) OR ``allow_no_ci: true``. With NEITHER, C2 falls into the legacy
    "every completed check must be success" mode, where the gate trusts whatever
    green check-runs happen to be present (published under the ``github-actions``
    slug) instead of a SPECIFIC named check the author cannot manufacture.

    The active PR-introduced-workflow vector is already closed at the classifier
    (a change to ``.github/workflows/`` routes to Quadrant D), so this is a
    defense-in-depth posture check rather than an open hole — hence a loud
    WARNING (not a hard load failure, which would reject otherwise-valid minimal
    configs). Operators should name ``required_checks`` (the reference config
    does); setting ``allow_no_ci: true`` is the conscious opt-out for a repo with
    no CI by design."""
    if config.env.allow_no_ci:
        return  # explicit, repo-wide opt-out: no CI by design
    ungated = [
        r for r in config.projects.supervised_repos
        if not config.projects.is_audit_only(r)
        and not config.projects.effective_required_checks(r, config.env.required_checks)
    ]
    if ungated:
        logger.warning(
            "C2 posture: gated repos with neither required_checks nor "
            "allow_no_ci will use the legacy 'all completed checks succeed' "
            "mode (no specific named check is required): %s. Name the CI "
            "check(s) each must pass (env.required_checks or "
            "projects.repo_overrides[<repo>].required_checks), or set "
            "env.allow_no_ci: true for a repo with no CI by design.",
            ", ".join(ungated),
        )
