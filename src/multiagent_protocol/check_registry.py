"""CLI for static and exact-baseline version-registry guards."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from multiagent_protocol.version_truth import strict_yaml
from multiagent_protocol.version_truth.completion import REGISTRY_BLOB_PATH, GitRunner
from multiagent_protocol.version_truth.registry_guard import validate_registry_guard

_FULL_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _git_output(runner: GitRunner, args: list[str], *, operation: str) -> bytes:
    result = runner.run(args)
    if result.exit_code != 0:
        raise RuntimeError(f"{operation}_failed_exit={result.exit_code}")
    return result.stdout


def _replace_refs(runner: GitRunner, repo_root: Path) -> list[str]:
    data = _git_output(
        runner,
        ["-C", str(repo_root), "for-each-ref", "--format=%(refname)", "refs/replace/"],
        operation="replace_ref_check",
    )
    return [line for line in data.decode("utf-8", errors="strict").splitlines() if line]


def _adr_loader(repo_root: Path):
    def load(path_value: str) -> dict:
        relative = strict_yaml.validate_safe_relpath(
            path_value, field="version_contract_supersession.adr"
        )
        path = (repo_root / relative).resolve()
        if repo_root.resolve() not in path.parents:
            raise ValueError("ADR path escapes repository")
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "---":
            raise ValueError("ADR frontmatter missing")
        try:
            end = lines.index("---", 1)
        except ValueError as exc:
            raise ValueError("ADR frontmatter unterminated") from exc
        value = strict_yaml.load_strict("\n".join(lines[1:end]) + "\n", source=relative)
        if not isinstance(value, dict):
            raise ValueError("ADR frontmatter must be a mapping")
        return value

    return load


def run_check(
    *,
    repo_root: Path,
    registry_path: Path,
    baseline_ref: str,
) -> tuple[dict, int]:
    reasons: list[str] = []
    binding: dict = {
        "registry_path": REGISTRY_BLOB_PATH,
        "baseline_ref": baseline_ref,
        "git_no_replace_objects": True,
    }
    dependency_blocked = False
    try:
        root = repo_root.resolve()
        candidate = registry_path if registry_path.is_absolute() else root / registry_path
        candidate = Path(os.path.abspath(candidate))
        canonical_path = root / REGISTRY_BLOB_PATH
        if candidate != canonical_path:
            reasons.append(f"registry_path_must_equal={REGISTRY_BLOB_PATH}")
            raise RuntimeError("invalid_registry_path")
        if candidate.is_symlink():
            reasons.append("registry_path_must_not_be_symlink")
            raise RuntimeError("invalid_registry_path")
        current_path = candidate.resolve(strict=True)
        if current_path != canonical_path or not current_path.is_file():
            reasons.append("registry_path_must_be_canonical_regular_file")
            raise RuntimeError("invalid_registry_path")
        relative_path = REGISTRY_BLOB_PATH
        if not _FULL_OID_RE.fullmatch(baseline_ref):
            reasons.append("baseline_ref_must_be_full_commit_oid")
            raise RuntimeError("invalid_baseline_ref")

        runner = GitRunner()
        replacement_refs_before = _replace_refs(runner, root)
        binding["replacement_refs_before"] = replacement_refs_before
        if replacement_refs_before:
            reasons.append("replacement_refs_not_empty")

        resolved_oid = (
            _git_output(
                runner,
                ["-C", str(root), "rev-parse", f"{baseline_ref}^{{commit}}"],
                operation="baseline_resolve",
            )
            .decode("ascii", errors="strict")
            .strip()
        )
        binding["baseline_commit_oid"] = resolved_oid
        if resolved_oid != baseline_ref:
            reasons.append("baseline_ref_did_not_resolve_to_itself")

        baseline_bytes = _git_output(
            runner,
            ["-C", str(root), "cat-file", "blob", f"{baseline_ref}:{relative_path}"],
            operation="baseline_registry_read",
        )
        current_bytes = current_path.read_bytes()
        binding.update(
            {
                "baseline_object_spec": f"{baseline_ref}:{relative_path}",
                "baseline_registry_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                "current_registry_sha256": hashlib.sha256(current_bytes).hexdigest(),
                "same_baseline_bytes_used_for_parse_and_hash": True,
            }
        )
        baseline_projects = strict_yaml.load_strict_bytes(
            baseline_bytes,
            source=binding["baseline_object_spec"],
            schema=lambda data: strict_yaml.parse_projects_registry(
                data, source=binding["baseline_object_spec"]
            ),
        )
        current_projects = strict_yaml.load_strict_bytes(
            current_bytes,
            source=relative_path,
            schema=lambda data: strict_yaml.parse_projects_registry(data, source=relative_path),
        )
        reasons.extend(
            validate_registry_guard(
                current_projects,
                previous=baseline_projects,
                load_adr=_adr_loader(root),
            )
        )
        replacement_refs_after = _replace_refs(runner, root)
        binding["replacement_refs_after"] = replacement_refs_after
        if replacement_refs_after:
            reasons.append("replacement_refs_not_empty")
    except strict_yaml.DependencyBlocked as exc:
        dependency_blocked = True
        reasons.append(str(exc))
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        if str(exc) not in {"invalid_baseline_ref", "invalid_registry_path"}:
            reasons.append(f"registry_guard_error={exc}")

    reasons = list(dict.fromkeys(reasons))
    status = (
        "DEPENDENCY_BLOCKED"
        if dependency_blocked
        else "REGISTRY_CHECK_OK"
        if not reasons
        else "REGISTRY_CHECK_FAILED"
    )
    exit_code = 4 if dependency_blocked else 0 if not reasons else 1
    return (
        {
            "kind": "RegistryCheck",
            "schema_version": 1,
            "status": status,
            "ok": exit_code == 0,
            "reasons": reasons,
            "input_binding": binding,
        },
        exit_code,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="multiagent-protocol check-registry",
        description="Guard version contracts against an exact baseline commit.",
    )
    parser.add_argument("--repo-root", default=".", help="Registry repository root.")
    parser.add_argument(
        "--registry",
        default=REGISTRY_BLOB_PATH,
        help=f"Canonical registry path (must be {REGISTRY_BLOB_PATH}).",
    )
    parser.add_argument(
        "--baseline-ref",
        required=True,
        help="Full 40- or 64-hex baseline commit OID; names and abbreviations fail.",
    )
    args = parser.parse_args(argv)
    payload, exit_code = run_check(
        repo_root=Path(args.repo_root),
        registry_path=Path(args.registry),
        baseline_ref=args.baseline_ref,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
