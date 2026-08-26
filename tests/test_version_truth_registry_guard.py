"""Tests for pure version-truth registry downgrade guards."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from multiagent_protocol.version_truth import registry_guard as registry_guard_module
from multiagent_protocol.version_truth import strict_yaml
from multiagent_protocol.version_truth.registry_guard import (
    validate_registry_guard,
    validate_registry_rows,
    validate_registry_transition,
)

PARITY = "legacy-declared-parity"
CANONICAL_STATE = "VERSION_STATE.yml"
TRANSITION_REASON = (
    "project-alpha: legacy-declared-parity transition requires accepted superseding ADR evidence"
)


def _parity_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "version_contract": PARITY,
        "version_state": CANONICAL_STATE,
    }
    row.update(overrides)
    return row


def _transition_row(
    *,
    to: Any = "release-manifest",
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"version_contract": to}
    if evidence is not None:
        row["version_contract_supersession"] = dict(evidence)
    return row


def _evidence(*, to: Any = "release-manifest") -> dict[str, Any]:
    return {
        "from": PARITY,
        "to": to,
        "adr": "docs/decisions/0001_version-contract.md",
    }


def _accepted_adr(*, to: Any = "release-manifest") -> dict[str, Any]:
    return {
        "status": "accepted",
        "supersedes": ["docs/decisions/0000_prior-contract.md"],
        "version_contract_supersession": {
            "project_id": "project-alpha",
            "from": PARITY,
            "to": to,
        },
    }


def test_valid_current_rows_have_no_reasons():
    projects = {
        "project-alpha": {
            **_parity_row(),
            "release_id_pattern": r"^rel-[0-9]+$",
        },
        "project-beta": {
            "version_contract": "release-manifest",
            "version_state": "ALT_VERSION_STATE.yml",
        },
    }

    assert validate_registry_rows(projects) == []


@pytest.mark.parametrize("pattern", [None, 7, "", "   "])
def test_release_id_pattern_must_be_a_nonempty_string(pattern: Any):
    projects = {"project-alpha": {"release_id_pattern": pattern}}

    assert validate_registry_rows(projects) == [
        "project-alpha: release_id_pattern must be a non-empty string"
    ]


@pytest.mark.parametrize(
    "pattern",
    [
        "[unterminated",
        "a{999999999999999999999999}",
    ],
)
def test_release_id_pattern_must_compile_without_exposing_parser_details(pattern: str):
    projects = {"project-alpha": {"release_id_pattern": pattern}}

    assert validate_registry_rows(projects) == [
        "project-alpha: release_id_pattern must be a valid regular expression"
    ]


def test_parity_omitted_version_state_uses_the_canonical_default():
    assert validate_registry_rows({"project-alpha": {"version_contract": PARITY}}) == []


@pytest.mark.parametrize("version_state", [None, "", "ALT_VERSION_STATE.yml"])
def test_parity_rejects_explicit_noncanonical_version_state(version_state: Any):
    row = {"version_contract": PARITY, "version_state": version_state}

    assert validate_registry_rows({"project-alpha": row}) == [
        "project-alpha: legacy-declared-parity requires version_state='VERSION_STATE.yml'"
    ]


def test_release_id_pattern_length_is_bounded():
    projects = {"project-alpha": {"release_id_pattern": "a" * 1025}}

    assert validate_registry_rows(projects) == [
        "project-alpha: release_id_pattern exceeds 1024 characters"
    ]


def test_release_id_pattern_at_the_length_boundary_is_accepted():
    assert validate_registry_rows(
        {"project-alpha": {"release_id_pattern": "a" * 1024}}
    ) == []


def test_release_id_pattern_recursion_failure_has_the_stable_invalid_reason(
    monkeypatch: pytest.MonkeyPatch,
):
    def recursion_failure(_value: str):
        raise RecursionError

    monkeypatch.setattr(registry_guard_module.re, "compile", recursion_failure)

    assert validate_registry_rows(
        {"project-alpha": {"release_id_pattern": "nested"}}
    ) == ["project-alpha: release_id_pattern must be a valid regular expression"]


def test_unchanged_parity_contract_needs_no_supersession_evidence():
    previous = {"project-alpha": _parity_row()}
    current = {"project-alpha": _parity_row()}

    assert validate_registry_transition(previous, current) == []


def test_non_parity_history_is_outside_the_downgrade_guard():
    previous = {"project-alpha": {"version_contract": "release-manifest"}}

    assert validate_registry_transition(previous, {}) == []


def test_prior_parity_row_deletion_is_rejected():
    previous = {"project-alpha": _parity_row()}

    assert validate_registry_transition(previous, {}) == [
        "project-alpha: prior legacy-declared-parity row was removed"
    ]


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {},
        {"from": PARITY, "to": "release-manifest", "adr": ""},
        {"from": "other", "to": "release-manifest", "adr": "decision.md"},
        {"from": PARITY, "to": "other", "adr": "decision.md"},
        {
            "from": PARITY,
            "to": "release-manifest",
            "adr": "decision.md",
            "extra": "not-exact",
        },
    ],
)
def test_transition_metadata_must_exactly_match(evidence: Mapping[str, Any] | None):
    previous = {"project-alpha": _parity_row()}
    current = {"project-alpha": _transition_row(evidence=evidence)}

    assert validate_registry_transition(
        previous,
        current,
        load_adr=lambda _path: _accepted_adr(),
    ) == [TRANSITION_REASON]


def test_mismatched_transition_metadata_does_not_load_an_adr():
    previous = {"project-alpha": _parity_row()}
    current = {
        "project-alpha": _transition_row(
            evidence={
                "from": PARITY,
                "to": "different-contract",
                "adr": "docs/decisions/0001_version-contract.md",
            }
        )
    }
    loaded_paths: list[str] = []

    assert validate_registry_transition(
        previous,
        current,
        load_adr=lambda path: loaded_paths.append(path) or _accepted_adr(),
    ) == [TRANSITION_REASON]
    assert loaded_paths == []


@pytest.mark.parametrize(
    "frontmatter",
    [
        {},
        {
            **_accepted_adr(),
            "status": "proposed",
        },
        {
            **_accepted_adr(),
            "supersedes": [],
        },
        {
            **_accepted_adr(),
            "supersedes": [""],
        },
        {
            **_accepted_adr(),
            "supersedes": "docs/decisions/0000_prior-contract.md",
        },
        {
            **_accepted_adr(),
            "version_contract_supersession": {
                "project_id": "project-beta",
                "from": PARITY,
                "to": "release-manifest",
            },
        },
        {
            **_accepted_adr(),
            "version_contract_supersession": {
                "project_id": "project-alpha",
                "from": PARITY,
                "to": "release-manifest",
                "extra": "not-exact",
            },
        },
    ],
)
def test_adr_frontmatter_must_be_accepted_and_exact(frontmatter: Mapping[str, Any]):
    previous = {"project-alpha": _parity_row()}
    current = {"project-alpha": _transition_row(evidence=_evidence())}

    assert validate_registry_transition(
        previous,
        current,
        load_adr=lambda _path: frontmatter,
    ) == [TRANSITION_REASON]


def test_adr_loader_failure_is_a_stable_fail_closed_reason():
    previous = {"project-alpha": _parity_row()}
    current = {"project-alpha": _transition_row(evidence=_evidence())}

    def unavailable(_path: str) -> Mapping[str, Any]:
        raise OSError("host-specific detail must not escape")

    assert validate_registry_transition(
        previous,
        current,
        load_adr=unavailable,
    ) == [TRANSITION_REASON]


def test_authorized_transition_loads_the_declared_adr_path():
    previous = {"project-alpha": _parity_row()}
    current = {"project-alpha": _transition_row(evidence=_evidence())}
    loaded_paths: list[str] = []

    def load_adr(path: str) -> Mapping[str, Any]:
        loaded_paths.append(path)
        return _accepted_adr()

    assert validate_registry_transition(previous, current, load_adr=load_adr) == []
    assert loaded_paths == ["docs/decisions/0001_version-contract.md"]


def test_strict_yaml_null_supersedes_cannot_authorize_a_transition():
    frontmatter = strict_yaml.load_strict(
        "status: accepted\n"
        "supersedes: null\n"
        "version_contract_supersession:\n"
        "  project_id: project-alpha\n"
        "  from: legacy-declared-parity\n"
        "  to: release-manifest\n"
    )
    previous = {"project-alpha": _parity_row()}
    current = {"project-alpha": _transition_row(evidence=_evidence())}

    assert frontmatter["supersedes"] == "null"
    assert validate_registry_transition(
        previous,
        current,
        load_adr=lambda _path: frontmatter,
    ) == [TRANSITION_REASON]


def test_strict_yaml_null_inside_supersedes_array_cannot_authorize_transition():
    frontmatter = strict_yaml.load_strict(
        "status: accepted\n"
        "supersedes:\n"
        "  - null\n"
        "version_contract_supersession:\n"
        "  project_id: project-alpha\n"
        "  from: legacy-declared-parity\n"
        "  to: release-manifest\n"
    )
    previous = {"project-alpha": _parity_row()}
    current = {"project-alpha": _transition_row(evidence=_evidence())}

    assert frontmatter["supersedes"] == ["null"]
    assert validate_registry_transition(
        previous,
        current,
        load_adr=lambda _path: frontmatter,
    ) == [TRANSITION_REASON]


def test_authorized_transition_to_missing_contract_uses_exact_null_marker():
    previous = {"project-alpha": _parity_row()}
    current = {
        "project-alpha": {
            "version_contract_supersession": _evidence(to=None),
        }
    }

    assert (
        validate_registry_transition(
            previous,
            current,
            load_adr=lambda _path: _accepted_adr(to=None),
        )
        == []
    )


def test_combined_guard_returns_static_then_historical_reasons():
    previous = {
        "project-alpha": _parity_row(),
        "project-beta": _parity_row(),
    }
    current = {
        "project-alpha": {
            "version_contract": "release-manifest",
            "release_id_pattern": "(",
        }
    }

    assert validate_registry_guard(current, previous=previous) == [
        "project-alpha: release_id_pattern must be a valid regular expression",
        TRANSITION_REASON,
        "project-beta: prior legacy-declared-parity row was removed",
    ]
