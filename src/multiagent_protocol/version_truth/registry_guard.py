"""Pure downgrade and static guards for portable version registries."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Any

from multiagent_protocol.version_truth import strict_yaml

PARITY_CONTRACT = "legacy-declared-parity"
CANONICAL_VERSION_STATE = "VERSION_STATE.yml"
_TRANSITION_REASON = (
    "{project_id}: legacy-declared-parity transition requires accepted superseding ADR evidence"
)


def validate_registry_rows(projects: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Validate registered regex syntax and the parity state-file invariant."""

    reasons: list[str] = []
    for project_id, row in projects.items():
        if "release_id_pattern" in row:
            pattern = row.get("release_id_pattern")
            if not isinstance(pattern, str) or not pattern.strip():
                reasons.append(f"{project_id}: release_id_pattern must be a non-empty string")
            elif len(pattern) > 1024:
                reasons.append(f"{project_id}: release_id_pattern exceeds 1024 characters")
            else:
                try:
                    re.compile(pattern)
                except (re.error, OverflowError):
                    reasons.append(
                        f"{project_id}: release_id_pattern must be a valid regular expression"
                    )
        if (
            row.get("version_contract") == PARITY_CONTRACT
            and row.get("version_state") != CANONICAL_VERSION_STATE
        ):
            reasons.append(
                f"{project_id}: {PARITY_CONTRACT} requires "
                f"version_state={CANONICAL_VERSION_STATE!r}"
            )
    return reasons


def _nonempty_supersedes(value: Any) -> bool:
    def valid_adr_path(item: Any) -> bool:
        if not isinstance(item, str) or not item.strip():
            return False
        try:
            relative = strict_yaml.validate_safe_relpath(item, field="supersedes")
        except strict_yaml.StrictYAMLError:
            return False
        return PurePosixPath(relative).suffix.lower() == ".md"

    return isinstance(value, list) and bool(value) and all(valid_adr_path(item) for item in value)


def _transition_is_authorized(
    *,
    project_id: str,
    before_contract: str,
    after_contract: Any,
    current_row: Mapping[str, Any],
    load_adr: Callable[[str], Mapping[str, Any]] | None,
) -> bool:
    evidence = current_row.get("version_contract_supersession")
    if not isinstance(evidence, Mapping) or set(evidence) != {"from", "to", "adr"}:
        return False
    adr_path = evidence.get("adr")
    if (
        evidence.get("from") != before_contract
        or evidence.get("to") != after_contract
        or not isinstance(adr_path, str)
        or not adr_path.strip()
        or load_adr is None
    ):
        return False
    try:
        frontmatter = load_adr(adr_path)
    except Exception:  # noqa: BLE001 - evidence resolution must fail closed
        return False
    if not isinstance(frontmatter, Mapping):
        return False
    marker = frontmatter.get("version_contract_supersession")
    if not isinstance(marker, Mapping) or set(marker) != {"project_id", "from", "to"}:
        return False
    return (
        frontmatter.get("status") == "accepted"
        and _nonempty_supersedes(frontmatter.get("supersedes"))
        and marker.get("project_id") == project_id
        and marker.get("from") == before_contract
        and marker.get("to") == after_contract
    )


def validate_registry_transition(
    previous: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    *,
    load_adr: Callable[[str], Mapping[str, Any]] | None = None,
) -> list[str]:
    """Reject removal or unapproved transition of every prior parity row."""

    reasons: list[str] = []
    for project_id, previous_row in previous.items():
        if previous_row.get("version_contract") != PARITY_CONTRACT:
            continue
        current_row = current.get(project_id)
        if current_row is None:
            reasons.append(f"{project_id}: prior {PARITY_CONTRACT} row was removed")
            continue
        after_contract = current_row.get("version_contract")
        if after_contract == PARITY_CONTRACT:
            continue
        if not _transition_is_authorized(
            project_id=project_id,
            before_contract=PARITY_CONTRACT,
            after_contract=after_contract,
            current_row=current_row,
            load_adr=load_adr,
        ):
            reasons.append(_TRANSITION_REASON.format(project_id=project_id))
    return reasons


def validate_registry_guard(
    current: Mapping[str, Mapping[str, Any]],
    *,
    previous: Mapping[str, Mapping[str, Any]] | None = None,
    load_adr: Callable[[str], Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return static reasons followed by exact-baseline transition reasons."""

    reasons = validate_registry_rows(current)
    if previous is not None:
        reasons.extend(validate_registry_transition(previous, current, load_adr=load_adr))
    return reasons
