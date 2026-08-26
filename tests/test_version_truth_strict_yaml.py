"""Fail-closed parser coverage for version-critical YAML bytes."""

from __future__ import annotations

import pytest

from multiagent_protocol.version_truth import strict_yaml


def test_plain_scalars_are_not_implicitly_typed():
    parsed = strict_yaml.load_strict("truthy: true\nnumber: 7\nnullish: null\n")

    assert parsed == {"truthy": "true", "number": "7", "nullish": "null"}


@pytest.mark.parametrize(
    "document",
    [
        "value: one\nvalue: two\n",
        "value: &shared one\n",
        "value: &shared one\nother: *shared\n",
        "base: &base\n  value: one\nmerged:\n  <<: *base\n",
        "value: !custom one\n",
        "value: one\n---\nvalue: two\n",
    ],
)
def test_ambiguous_or_multi_document_yaml_is_rejected(document: str):
    with pytest.raises(strict_yaml.StrictYAMLError):
        strict_yaml.load_strict(document, source="fixture.yml")


def test_duplicate_project_ids_are_rejected():
    data = strict_yaml.load_strict(
        "schema_version: 1\nprojects:\n  - id: duplicate\n  - id: duplicate\n"
    )

    with pytest.raises(strict_yaml.StrictYAMLError, match="duplicate project id"):
        strict_yaml.parse_projects_registry(data)


@pytest.mark.parametrize(
    "path",
    ["/absolute.yml", "../escape.yml", "nested/../../escape.yml", "C:/drive.yml", "bad\0.yml"],
)
def test_version_evidence_paths_must_be_safe_relative_paths(path: str):
    with pytest.raises(strict_yaml.StrictYAMLError):
        strict_yaml.validate_safe_relpath(path)
