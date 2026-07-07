"""Doctrine ↔ code consistency tests.

Catches the kind of drift that reviewers flagged in the v0.0.x audit:
doctrine docs cite config keys that the schemas do not define, or skills
that the codebase does not ship.

This test deliberately scans the doctrine *and* the code together, so a
PR that adds a new ``config.foo.bar`` reference to a concept doc without
adding ``foo.bar`` to the right JSON schema, or claims a built-in skill
that does not exist on disk, fails CI.

The goal is to make doc-vs-code drift a CI failure, not a sub-agent
review finding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONCEPTS = ROOT / "docs" / "concepts"
SCHEMAS = ROOT / "schemas"
BUILTIN_SKILLS = ROOT / "src" / "multiagent_protocol" / "skills" / "builtin"


# ---- 1. No dotted `config.foo.bar` references in doctrine -------------------

# Patterns we accept in doctrine (these are not config refs):
ALLOWED_DOTTED = re.compile(
    r"^(config/|config\.skills\.disabled|"
    r"config\.skills\.severity_overrides)$"
)

# Find any backticked literal that starts with "config." and has at least
# one further dot. These are the kind of references reviewers flagged
# (config.governance.repository, config.decision_inbox.repository, etc.)
# — they look authoritative but were never in any schema.
CONFIG_REF_RE = re.compile(r"`(config\.[a-z_]+\.[a-z_.]+)`")


def _collect_doctrine_files() -> list[Path]:
    return sorted(CONCEPTS.rglob("*.md")) + sorted((ROOT / "docs" / "guide").rglob("*.md"))


def test_no_undefined_dotted_config_keys_in_doctrine():
    """Doctrine must not reference `config.foo.bar` paths that schemas
    do not define. We do allow:

      - ``config/owner.yml``, ``config/projects.yml``, etc. (slash form
        — points at the actual file).
      - ``config.skills.disabled``, ``config.skills.severity_overrides``
        (these *are* keys under the loaded ``AppConfig.skills`` dataclass).

    Anything else with the form ``config.X.Y`` is suspected drift.
    """
    findings: list[str] = []
    for path in _collect_doctrine_files():
        text = path.read_text(encoding="utf-8")
        for m in CONFIG_REF_RE.finditer(text):
            ref = m.group(1)
            if ALLOWED_DOTTED.match(ref):
                continue
            line = text.count("\n", 0, m.start()) + 1
            findings.append(
                f"{path.relative_to(ROOT)}:{line}: doctrine references "
                f"`{ref}` but no schema defines this key. Either add it "
                f"to the right *.schema.json or rewrite the doctrine to "
                f"use the actual config-file path (`config/<file>.yml` "
                f"`<field>`)."
            )

    assert not findings, (
        "Doctrine ↔ schema drift:\n  " + "\n  ".join(findings)
    )


# ---- 2. Every "Built-in:" skill mentioned in skills-plugin.md must exist ----

# Match lines like:
#   `validator_owner_approval.py`     | C3 — owner approval ...
#   `hook_hallucination_guard.py`     | L5 ...
BUILTIN_LISTING_RE = re.compile(r"`([a-z_]+_[a-z_]+\.py)`")


def test_every_doctrine_listed_builtin_skill_exists():
    """skills-plugin.md and general-preferences.md list built-in skills
    by filename. Each name must point to a real file in
    src/multiagent_protocol/skills/builtin/.
    """
    findings: list[str] = []
    for doc_name in ("skills-plugin.md", "general-preferences.md"):
        path = CONCEPTS / doc_name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Only count names that look like skill files (have the
        # validator_/classifier_/hook_ prefix the codebase uses).
        for m in BUILTIN_LISTING_RE.finditer(text):
            name = m.group(1)
            if not (
                name.startswith("validator_")
                or name.startswith("classifier_")
                or name.startswith("hook_")
            ):
                continue
            on_disk = BUILTIN_SKILLS / name
            if not on_disk.exists():
                line = text.count("\n", 0, m.start()) + 1
                findings.append(
                    f"{path.relative_to(ROOT)}:{line}: doctrine claims "
                    f"built-in skill `{name}` exists, but "
                    f"{on_disk.relative_to(ROOT)} is missing. Either add "
                    f"the file or remove the claim from the doctrine."
                )

    assert not findings, (
        "Doctrine ↔ skills drift:\n  " + "\n  ".join(findings)
    )


# ---- 3. Examples must validate against schemas ------------------------------

EXAMPLES_DIR = ROOT / "examples"


@pytest.fixture(scope="module")
def schemas() -> dict:
    return {
        "owner.yml": json.loads((SCHEMAS / "owner.schema.json").read_text()),
        "projects.yml": json.loads((SCHEMAS / "projects.schema.json").read_text()),
        "env.yml": json.loads((SCHEMAS / "env.schema.json").read_text()),
        "skills.yml": json.loads((SCHEMAS / "skills.schema.json").read_text()),
        "agent_registry.yml": json.loads(
            (SCHEMAS / "agent_registry.schema.json").read_text()
        ),
        "preferences.yml": json.loads(
            (SCHEMAS / "preferences.schema.json").read_text()
        ),
    }


def test_every_example_validates(schemas):
    """Every example/<NAME>/config/*.yml must validate against its schema."""
    import yaml
    from jsonschema import validate as jsonschema_validate

    findings: list[str] = []
    for example_dir in sorted(EXAMPLES_DIR.iterdir()):
        if not example_dir.is_dir():
            continue
        config_dir = example_dir / "config"
        if not config_dir.exists():
            continue
        for yml_path in sorted(config_dir.glob("*.yml")):
            schema = schemas.get(yml_path.name)
            if schema is None:
                continue
            data = yaml.safe_load(yml_path.read_text(encoding="utf-8")) or {}
            try:
                jsonschema_validate(instance=data, schema=schema)
            except Exception as e:
                findings.append(
                    f"{yml_path.relative_to(ROOT)}: validation failed: {e}"
                )

    assert not findings, (
        "Example × schema validation failures:\n  " + "\n  ".join(findings)
    )
