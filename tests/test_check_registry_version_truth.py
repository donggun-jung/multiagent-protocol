"""Integration coverage for the exact-baseline registry guard CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

from multiagent_protocol.check_registry import run_check

REGISTRY_PROJECTION = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "version_truth"
    / "current_registry_projection.yml"
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _registry(
    *,
    contract: str = "legacy-declared-parity",
    version_state: str | None = "VERSION_STATE.yml",
    pattern: str = "^rel-[0-9]+$",
    supersession: str = "",
) -> str:
    state_line = f"    version_state: {version_state}\n" if version_state is not None else ""
    return (
        "schema_version: 1\n"
        "projects:\n"
        "  - id: project-alpha\n"
        f"    version_contract: {contract}\n"
        f"{state_line}"
        f'    release_id_pattern: "{pattern}"\n'
        f"{supersession}"
    )


def _repo(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "repo"
    registry = root / "governance" / "projects.yml"
    registry.parent.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    registry.write_text(_registry(), encoding="utf-8")
    _git(root, "add", "governance/projects.yml")
    _git(root, "commit", "-q", "-m", "baseline")
    return root, registry, _git(root, "rev-parse", "HEAD")


def test_registry_guard_accepts_unchanged_exact_baseline(tmp_path: Path):
    root, registry, baseline = _repo(tmp_path)

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 0
    assert receipt["kind"] == "RegistryCheck"
    assert receipt["status"] == "REGISTRY_CHECK_OK"
    assert receipt["reasons"] == []
    assert receipt["input_binding"]["baseline_commit_oid"] == baseline
    assert receipt["input_binding"]["same_baseline_bytes_used_for_parse_and_hash"] is True
    assert receipt["unverified_dimensions"] == [
        "superseding_adr_substantive_review_and_merge_authorization"
    ]


def test_registry_guard_accepts_an_omitted_parity_state_path_as_canonical_default(
    tmp_path: Path,
):
    root, registry, baseline = _repo(tmp_path)
    registry.write_text(_registry(version_state=None), encoding="utf-8")

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 0
    assert receipt["status"] == "REGISTRY_CHECK_OK"
    assert receipt["reasons"] == []


def test_registry_guard_accepts_the_redacted_current_registry_projection_unchanged(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    registry = root / "governance" / "projects.yml"
    registry.parent.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    registry.write_bytes(REGISTRY_PROJECTION.read_bytes())
    _git(root, "add", "governance/projects.yml")
    _git(root, "commit", "-q", "-m", "projected registry")
    baseline = _git(root, "rev-parse", "HEAD")

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 0
    assert receipt["status"] == "REGISTRY_CHECK_OK"
    assert receipt["reasons"] == []
    assert (
        receipt["input_binding"]["baseline_registry_sha256"]
        == receipt["input_binding"]["current_registry_sha256"]
    )


def test_registry_guard_requires_a_full_exact_commit_oid(tmp_path: Path):
    root, registry, baseline = _repo(tmp_path)

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline[:12],
    )

    assert exit_code == 1
    assert receipt["reasons"] == ["baseline_ref_must_be_full_commit_oid"]


def test_registry_guard_rejects_noncanonical_registry_path(tmp_path: Path):
    root, _registry_path, baseline = _repo(tmp_path)
    alternate = root / "governance" / "alternate.yml"
    alternate.write_text(_registry(), encoding="utf-8")

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=alternate,
        baseline_ref=baseline,
    )

    assert exit_code == 1
    assert receipt["reasons"] == ["registry_path_must_equal=governance/projects.yml"]


def test_registry_guard_rejects_symlink_rebinding_of_canonical_registry(tmp_path: Path):
    root, registry, _baseline = _repo(tmp_path)
    alternate = root / "governance" / "alternate.yml"
    alternate.write_text(
        _registry(contract="release-manifest"),
        encoding="utf-8",
    )
    _git(root, "add", "governance/alternate.yml")
    _git(root, "commit", "-q", "--amend", "--no-edit")
    baseline = _git(root, "rev-parse", "HEAD")
    registry.unlink()
    registry.symlink_to("alternate.yml")

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 1
    assert receipt["reasons"] == ["registry_path_must_not_be_symlink"]


def test_registry_guard_rejects_invalid_registered_pattern(tmp_path: Path):
    root, registry, baseline = _repo(tmp_path)
    registry.write_text(_registry(pattern="[unterminated"), encoding="utf-8")

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 1
    assert any(
        "release_id_pattern must be a valid regular expression" in reason
        for reason in receipt["reasons"]
    )


def test_registry_guard_rejects_alternate_parity_state_path(tmp_path: Path):
    root, registry, baseline = _repo(tmp_path)
    registry.write_text(_registry(version_state="state/ALT.yml"), encoding="utf-8")

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 1
    assert any(
        "requires version_state='VERSION_STATE.yml'" in reason for reason in receipt["reasons"]
    )


def test_registry_guard_rejects_unapproved_parity_transition(tmp_path: Path):
    root, registry, baseline = _repo(tmp_path)
    registry.write_text(_registry(contract="release-manifest"), encoding="utf-8")

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 1
    assert any(
        "requires accepted superseding ADR evidence" in reason for reason in receipt["reasons"]
    )


def test_registry_guard_accepts_exact_superseding_adr_evidence(tmp_path: Path):
    root, registry, baseline = _repo(tmp_path)
    evidence = (
        "    version_contract_supersession:\n"
        "      from: legacy-declared-parity\n"
        "      to: release-manifest\n"
        "      adr: docs/decisions/0001_version-contract.md\n"
    )
    registry.write_text(
        _registry(contract="release-manifest", supersession=evidence),
        encoding="utf-8",
    )
    decision = root / "docs" / "decisions" / "0001_version-contract.md"
    decision.parent.mkdir(parents=True)
    decision.write_text(
        "---\n"
        "status: accepted\n"
        "supersedes:\n"
        "  - docs/decisions/0000-prior.md\n"
        "version_contract_supersession:\n"
        "  project_id: project-alpha\n"
        "  from: legacy-declared-parity\n"
        "  to: release-manifest\n"
        "---\n"
        "# Version contract decision\n",
        encoding="utf-8",
    )

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 0
    assert receipt["status"] == "REGISTRY_CHECK_OK"
    assert "authorized_transition_count" not in receipt["input_binding"]


def test_registry_guard_detects_replace_refs_while_disabling_them(tmp_path: Path):
    root, registry, baseline = _repo(tmp_path)
    registry.write_text(_registry(pattern="^build-[0-9]+$"), encoding="utf-8")
    _git(root, "commit", "-q", "-am", "replacement target")
    replacement = _git(root, "rev-parse", "HEAD")
    _git(root, "replace", baseline, replacement)
    registry.write_text(_registry(), encoding="utf-8")

    receipt, exit_code = run_check(
        repo_root=root,
        registry_path=registry,
        baseline_ref=baseline,
    )

    assert exit_code == 1
    assert "replacement_refs_not_empty" in receipt["reasons"]
    assert receipt["input_binding"]["baseline_commit_oid"] == baseline
