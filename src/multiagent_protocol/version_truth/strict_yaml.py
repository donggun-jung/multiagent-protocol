"""Strict YAML parsing for version-critical registry and state files.

The loader deliberately disables YAML implicit scalar typing and rejects
duplicates, aliases, anchors, merge keys, custom tags, and multiple documents.
Callers therefore parse the exact bytes they hash without a permissive fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

try:
    import yaml as _yaml
    from yaml.events import AliasEvent as _AliasEvent
    from yaml.nodes import MappingNode as _MappingNode

    _YAML_IMPORT_ERROR: BaseException | None = None
except Exception as exc:  # pragma: no cover - exercised in dependency-isolation tests
    _yaml = None
    _AliasEvent = None
    _MappingNode = None
    _YAML_IMPORT_ERROR = exc


T = TypeVar("T")


class StrictYAMLError(ValueError):
    """A YAML encoding, structure, or typed-schema violation."""


class DependencyBlocked(RuntimeError):
    """PyYAML is unavailable, so version-critical parsing must stop."""

    def __init__(self, cause: BaseException | None = None) -> None:
        super().__init__(
            "PyYAML is required for version-truth checks; install "
            "multiagent-protocol with its declared runtime dependencies. "
            "No fallback parser exists."
        )
        self.cause = cause


_STRICT_LOADER: type | None = None


def _require_yaml() -> None:
    if _yaml is None:
        raise DependencyBlocked(_YAML_IMPORT_ERROR)


def _loader_type() -> type:
    global _STRICT_LOADER
    _require_yaml()
    if _STRICT_LOADER is not None:
        return _STRICT_LOADER

    class StrictLoader(_yaml.SafeLoader):
        yaml_implicit_resolvers: dict = {}

        def compose_node(self, parent, index):
            if self.check_event(_AliasEvent):
                event = self.get_event()
                raise StrictYAMLError(f"YAML alias '*{event.anchor}' is not allowed")
            event = self.peek_event()
            if getattr(event, "anchor", None) is not None:
                raise StrictYAMLError(f"YAML anchor '&{event.anchor}' is not allowed")
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            if not isinstance(node, _MappingNode):
                raise StrictYAMLError(f"expected a mapping node, found {node.id}")
            mapping: dict[Any, Any] = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if isinstance(key, (list, dict)):
                    raise StrictYAMLError(f"unsupported non-scalar mapping key: {key!r}")
                if key == "<<":
                    raise StrictYAMLError("YAML merge key '<<' is not allowed")
                if key in mapping:
                    raise StrictYAMLError(f"duplicate mapping key: {key!r}")
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    _STRICT_LOADER = StrictLoader
    return StrictLoader


def load_strict(
    text: str,
    *,
    source: str = "<string>",
    schema: Callable[[Any], T] | None = None,
) -> T | Any:
    """Parse exactly one YAML document under the strict contract."""

    loader = _loader_type()(text)
    try:
        try:
            data = loader.get_single_data()
        except StrictYAMLError:
            raise
        except _yaml.YAMLError as exc:
            raise StrictYAMLError(f"{source}: {exc}") from exc
    finally:
        loader.dispose()
    return schema(data) if schema is not None else data


def load_strict_bytes(
    data: bytes,
    *,
    source: str,
    schema: Callable[[Any], T] | None = None,
) -> T | Any:
    """Decode UTF-8 and parse the same byte string the caller hashes."""

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise StrictYAMLError(f"{source}: not valid UTF-8: {exc}") from exc
    return load_strict(text, source=source, schema=schema)


def load_strict_file(
    path: str | Path,
    *,
    schema: Callable[[Any], T] | None = None,
) -> T | Any:
    file_path = Path(path)
    return load_strict_bytes(file_path.read_bytes(), source=str(file_path), schema=schema)


def _as_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise StrictYAMLError(f"{field}: expected integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise StrictYAMLError(f"{field}: expected integer, got {value!r}")


def parse_projects_registry(
    data: Any,
    *,
    source: str = "governance/projects.yml",
) -> dict[str, dict[str, Any]]:
    """Validate the portable version registry and index rows by project id."""

    if not isinstance(data, dict):
        raise StrictYAMLError(f"{source}: top level must be a mapping")
    if _as_int(data.get("schema_version"), field=f"{source}:schema_version") != 1:
        raise StrictYAMLError(f"{source}: unsupported schema_version")
    rows = data.get("projects")
    if not isinstance(rows, list) or not rows:
        raise StrictYAMLError(f"{source}: projects must be a non-empty list")
    projects: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise StrictYAMLError(f"{source}: projects[{index}] must be a mapping")
        project_id = row.get("id")
        if not isinstance(project_id, str) or not project_id:
            raise StrictYAMLError(f"{source}: projects[{index}].id must be a string")
        if project_id in projects:
            raise StrictYAMLError(f"{source}: duplicate project id {project_id!r}")
        projects[project_id] = row
    return projects


def parse_flat_state(data: Any, *, source: str = "VERSION_STATE.yml") -> dict[str, Any]:
    """Validate a flat state mapping with scalar or scalar-list values."""

    if not isinstance(data, dict):
        raise StrictYAMLError(f"{source}: top level must be a mapping")
    for key, value in data.items():
        if isinstance(value, dict):
            raise StrictYAMLError(f"{source}: {key!r} must not be a mapping")
        if isinstance(value, list) and any(isinstance(item, (list, dict)) for item in value):
            raise StrictYAMLError(f"{source}: {key!r} must contain scalar list items")
    return data


def validate_safe_relpath(value: Any, *, field: str = "path") -> str:
    """Reject absolute, traversal, NUL, and platform-drive paths."""

    if not isinstance(value, str) or not value:
        raise StrictYAMLError(f"{field}: expected a non-empty relative path")
    if "\x00" in value:
        raise StrictYAMLError(f"{field}: path contains NUL")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith(("/", "\\")):
        raise StrictYAMLError(f"{field}: absolute path is not allowed")
    if len(value) > 1 and value[1] == ":":
        raise StrictYAMLError(f"{field}: drive-qualified path is not allowed")
    if ".." in path.parts:
        raise StrictYAMLError(f"{field}: parent traversal is not allowed")
    return value
