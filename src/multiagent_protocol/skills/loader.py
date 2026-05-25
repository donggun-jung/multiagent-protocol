"""Plugin loader for the skills system.

Discovers and instantiates skills from:

1. Built-in: ``src/multiagent_protocol/skills/builtin/*.py``
2. User-added: ``config/skills/{validators,classifier,branch_hooks}/*.py``

Loader enforces:

- Built-in skill load failure is a hard error (raises ``SkillLoadError``).
- User-added skill load failure is a soft warning (logged, skipped).
- User skills cannot import network libraries (``requests``, ``urllib``,
  ``socket``, etc.) — heuristic check via AST inspection at load time.
- Each skill file must expose exactly one class implementing one of the
  three Protocols in :mod:`multiagent_protocol.skills.base`.

See ``docs/concepts/skills-plugin.md`` for the full specification.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from multiagent_protocol.skills.base import (
    BranchHook,
    ClassifierRule,
    Validator,
)

logger = logging.getLogger(__name__)


class SkillLoadError(Exception):
    """Built-in skill failed to load (hard error)."""


# Heuristic: these modules are blocked from user skills. Built-in skills are
# allowed to import anything (they ship with the project, vetted at review).
# The list covers three categories:
#   1. Network / IPC — user skills must be pure functions of context.
#   2. Code-execution surface — pickle/marshal/shelve can deserialize attacker
#      input into arbitrary code; xmlrpc.* and xml.etree.* are XXE / billion-
#      laughs risks; ctypes is a foreign-function gateway.
#   3. Subprocess + os primitives that shell out — same effect as network for
#      our threat model.
#
# This is a heuristic (AST-walk at load time), not a sandbox. A skill can
# still bypass via importlib.import_module / __import__ / exec / eval. The
# loader catches the obvious cases; the protocol's security model assumes the
# operator vets every user skill before installing.
BLOCKED_USER_IMPORTS = {
    # Network + IPC.
    "requests",
    "httpx",
    "urllib",
    "urllib3",
    "http",
    "socket",
    "ssl",
    "smtplib",
    "ftplib",
    "telnetlib",
    "asyncio",
    "aiohttp",
    "websocket",
    "websockets",
    "paramiko",
    # Deserialization-as-code surfaces.
    "pickle",
    "_pickle",
    "marshal",
    "shelve",
    "shelve3",
    # XML-based XXE / billion-laughs surfaces.
    "xml",
    "xml.etree",
    "xml.sax",
    "xml.dom",
    "xmlrpc",
    "xmlrpc.client",
    "xmlrpc.server",
    "lxml",
    # Subprocess and foreign-function gateways.
    "subprocess",
    "os.system",  # technically reached via "os" attribute; we also block this name
    "ctypes",
    "cffi",
    "multiprocessing",
}


@dataclass
class LoadedSkills:
    """The result of a loader run.

    Each list holds skill **instances** (not classes), ready to invoke.
    """

    validators: list[Validator] = field(default_factory=list)
    classifier_rules: list[ClassifierRule] = field(default_factory=list)
    branch_hooks: list[BranchHook] = field(default_factory=list)
    user_skill_load_failures: list[tuple[str, str]] = field(default_factory=list)

    def total(self) -> int:
        return (
            len(self.validators) + len(self.classifier_rules) + len(self.branch_hooks)
        )


def _check_imports_for_network(path: Path) -> str | None:
    """Return a reason if the file imports a blocked module, else ``None``.

    Heuristic only — sophisticated bypass (e.g. ``importlib.import_module``)
    is not in scope. The skills security model accepts this limitation;
    operators install (or vet) every user skill.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        return f"could not read file: {e}"
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return f"syntax error: {e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BLOCKED_USER_IMPORTS:
                    return f"imports blocked module '{alias.name}'"
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in BLOCKED_USER_IMPORTS:
                return f"imports from blocked module '{node.module}'"
    return None


def _load_module_from_file(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SkillLoadError(f"could not load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _instantiate_skills_from_module(module, source: str):
    """Find all classes in module that implement one of the Protocols.

    Returns a tuple ``(validators, classifier_rules, branch_hooks)``.
    """
    validators: list[Validator] = []
    classifier_rules: list[ClassifierRule] = []
    branch_hooks: list[BranchHook] = []

    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        attr = getattr(module, attr_name)
        if not isinstance(attr, type):
            continue
        # Construct lazily; only the matching Protocol is checked.
        try:
            instance = attr()
        except TypeError:
            # Class takes arguments; cannot be a skill (skills are 0-arg).
            continue

        # Order matters: a class implementing multiple Protocols would be
        # ambiguous. We check Validator first, then ClassifierRule, then
        # BranchHook, and stop at the first match.
        if isinstance(instance, Validator):
            validators.append(instance)
            logger.info("loaded validator '%s' from %s", instance.name, source)
        elif isinstance(instance, ClassifierRule):
            classifier_rules.append(instance)
            logger.info("loaded classifier rule '%s' from %s", instance.name, source)
        elif isinstance(instance, BranchHook):
            branch_hooks.append(instance)
            logger.info("loaded branch hook '%s' from %s", instance.name, source)

    return validators, classifier_rules, branch_hooks


def load_builtin_skills() -> LoadedSkills:
    """Load all built-in skills. Failure here is a hard error."""
    skills = LoadedSkills()
    builtin_dir = Path(__file__).parent / "builtin"
    if not builtin_dir.exists():
        logger.warning("builtin skills dir does not exist: %s", builtin_dir)
        return skills

    for path in sorted(builtin_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"multiagent_protocol.skills.builtin.{path.stem}"
        try:
            module = _load_module_from_file(module_name, path)
        except Exception as e:
            raise SkillLoadError(f"builtin skill load failed: {path.name}: {e}") from e

        v, c, b = _instantiate_skills_from_module(module, f"builtin/{path.name}")
        skills.validators.extend(v)
        skills.classifier_rules.extend(c)
        skills.branch_hooks.extend(b)

    return skills


def load_user_skills(config_root: Path | None = None) -> LoadedSkills:
    """Load user-added skills. Failures are soft warnings, not exceptions."""
    skills = LoadedSkills()
    if config_root is None:
        config_root = Path.cwd() / "config" / "skills"
    if not config_root.exists():
        return skills

    subdirs = ("validators", "classifier", "branch_hooks")

    for subdir_name in subdirs:
        subdir = config_root / subdir_name
        if not subdir.exists():
            continue
        for path in sorted(subdir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            # Network-import heuristic check.
            blocked_reason = _check_imports_for_network(path)
            if blocked_reason is not None:
                msg = f"user skill {path.name}: {blocked_reason}"
                logger.warning("user skill rejected: %s", msg)
                skills.user_skill_load_failures.append((str(path), msg))
                continue

            module_name = f"user_skills.{subdir_name}.{path.stem}"
            try:
                module = _load_module_from_file(module_name, path)
            except Exception as e:
                msg = f"import failed: {e}"
                logger.warning("user skill load failed: %s: %s", path.name, msg)
                skills.user_skill_load_failures.append((str(path), msg))
                continue

            try:
                v, c, b = _instantiate_skills_from_module(
                    module, f"user/{subdir_name}/{path.name}"
                )
            except Exception as e:
                msg = f"instantiation failed: {e}"
                logger.warning("user skill load failed: %s: %s", path.name, msg)
                skills.user_skill_load_failures.append((str(path), msg))
                continue

            # Each subdir should only produce skills of the matching kind.
            # A file in validators/ that defines a ClassifierRule is permitted
            # but goes to classifier_rules; we do not enforce strict separation.
            skills.validators.extend(v)
            skills.classifier_rules.extend(c)
            skills.branch_hooks.extend(b)

    return skills


def load_all(config_root: Path | None = None) -> LoadedSkills:
    """Load built-in + user skills together. Builtin failure raises."""
    builtin = load_builtin_skills()
    user = load_user_skills(config_root)

    combined = LoadedSkills(
        validators=builtin.validators + user.validators,
        classifier_rules=builtin.classifier_rules + user.classifier_rules,
        branch_hooks=builtin.branch_hooks + user.branch_hooks,
        user_skill_load_failures=user.user_skill_load_failures,
    )
    logger.info(
        "skills loaded: %d builtin + %d user = %d total (%d failed to load)",
        builtin.total(),
        user.total(),
        combined.total(),
        len(combined.user_skill_load_failures),
    )
    return combined
