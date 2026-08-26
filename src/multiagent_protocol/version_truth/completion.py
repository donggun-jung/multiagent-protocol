"""Atomic Git bindings and self-describing completion receipts.

This module verifies declared version state only. It intentionally does not
claim that a deployment happened, that a live endpoint serves those bytes, or
that a caller-provided task identifier is authentic. Those dimensions are
listed in every receipt instead of being implied by a green binding result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from multiagent_protocol.version_truth import strict_yaml

REGISTRY_BLOB_PATH = "governance/projects.yml"
VERSION_STATE_PATH = "VERSION_STATE.yml"
MAIN_REFSPEC = "refs/heads/main"
LOCAL_FETCH_REF = "refs/heads/completion-main"
COMPLETION_STATUS_OK = "DECLARED_STATE_COMPLETION_SUBRECEIPT_OK"
COMPLETION_STATUS_BLOCKED = "DECLARED_STATE_COMPLETION_SUBRECEIPT_BLOCKED"
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REMOTE_RE = re.compile(
    r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_PROJECT_STRING_FIELDS = (
    "repo",
    "repo_slug",
    "tier",
    "active_version",
    "active_version_type",
    "deployed_baseline",
    "start_command",
    "finish_command",
    "claim_command",
    "cross_project_role",
    "secrets_mode",
)

VERIFIED_DIMENSIONS = [
    "registry_origin_main_tip_and_exact_blob_bytes",
    "product_origin_main_tip_stability",
    "product_version_state_exact_blob_bytes",
    "declared_release_identifier_fullmatch",
    "registry_product_declared_version_parity",
    "git_replacement_refs_absent_and_disabled",
    "receipt_content_sha256",
]

UNVERIFIED_DIMENSIONS = [
    "deployment_instance_causality",
    "live_identity_and_source_readback",
    "deploy_source_to_artifact_provenance",
    "deployment_task_id_authenticity",
    "generated_at_clock_authenticity",
    "trusted_nonce_authenticity_and_uniqueness",
    "authoritative_monotonic_deployment_sequence",
    "post_readback_to_verdict_deployment_serialization",
    "registry_origin_root_of_trust",
    "remote_tip_aba_between_probes",
    "post_final_probe_remote_or_live_change",
    "local_execution_host_and_git_binary_integrity",
    "receipt_storage_authenticity_and_immutability",
    "final_process_exit_observation_after_receipt_emit",
]

RECEIPT_SHA256_METHOD = {
    "algorithm": "SHA-256",
    "canonicalization": "UTF-8 JSON; sort_keys=true; separators=(',', ':'); ensure_ascii=false",
    "excluded_json_pointer": "/receipt_sha256",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_slug(remote_url: str) -> str | None:
    match = _REMOTE_RE.fullmatch(remote_url.strip())
    if match is None:
        return None
    return f"{match.group('owner')}/{match.group('name')}"


def registry_url_for_slug(slug: str) -> str:
    return f"https://github.com/{slug}.git"


def _canonical_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    value = copy.deepcopy(receipt)
    value.pop("receipt_sha256", None)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def seal_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return ``receipt`` with a reproducible self-content SHA-256."""

    receipt["receipt_sha256_method"] = dict(RECEIPT_SHA256_METHOD)
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = hashlib.sha256(_canonical_receipt_bytes(receipt)).hexdigest()
    return receipt


def verify_receipt_sha256(receipt: dict[str, Any]) -> bool:
    digest = receipt.get("receipt_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    if receipt.get("receipt_sha256_method") != RECEIPT_SHA256_METHOD:
        return False
    return hashlib.sha256(_canonical_receipt_bytes(receipt)).hexdigest() == digest


def _git_blob_oid(data: bytes, object_format: str) -> str:
    if object_format not in {"sha1", "sha256"}:
        raise ValueError(f"unsupported_git_object_format={object_format}")
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.new(object_format, header + data).hexdigest()


@dataclass(frozen=True)
class GitCommandResult:
    argv: list[str]
    started_at_utc: str
    completed_at_utc: str
    exit_code: int
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)

    def receipt_fields(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "exit_code": self.exit_code,
        }


class GitRunner:
    """Run Git with replacement objects disabled and config injection scrubbed."""

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock

    @staticmethod
    def environment() -> dict[str, str]:
        env = dict(os.environ)
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_SHALLOW_FILE",
            "GIT_NAMESPACE",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_PARAMETERS",
            "GIT_EXEC_PATH",
            "GIT_PROXY_COMMAND",
            "GIT_SSH",
            "GIT_SSH_COMMAND",
            "GIT_SSH_VARIANT",
            "GIT_ASKPASS",
            "SSH_ASKPASS",
            "GIT_SSL_NO_VERIFY",
            "GIT_SSL_CAINFO",
            "GIT_SSL_CAPATH",
        ):
            env.pop(key, None)
        for key in tuple(env):
            if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
                env.pop(key, None)
        env["GIT_NO_REPLACE_OBJECTS"] = "1"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def run(self, args: list[str], *, cwd: Path | None = None) -> GitCommandResult:
        argv = ["git", *args]
        started = format_utc(self._clock())
        process = subprocess.run(
            argv,
            cwd=cwd,
            env=self.environment(),
            capture_output=True,
            check=False,
        )
        completed = format_utc(self._clock())
        return GitCommandResult(
            argv=argv,
            started_at_utc=started,
            completed_at_utc=completed,
            exit_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )


class BindingFailure(RuntimeError):
    def __init__(self, *, label: str, reason: str, partial: dict[str, Any]) -> None:
        super().__init__(reason)
        self.label = label
        self.reason = reason
        self.partial = partial


def _require_success(
    result: GitCommandResult,
    *,
    label: str,
    operation: str,
    partial: dict[str, Any],
) -> bytes:
    if result.exit_code != 0:
        raise BindingFailure(
            label=label,
            reason=f"{label}_{operation}_failed_exit={result.exit_code}",
            partial=partial,
        )
    return result.stdout


def _decode_ascii_line(data: bytes, *, label: str, field_name: str) -> str:
    try:
        value = data.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label}_{field_name}_not_ascii") from exc
    return value


@dataclass
class RemoteBinding:
    label: str
    remote_url: str
    origin_slug: str
    refspec: str
    tip_oid_before: str
    fetched_oid: str
    object_format: str
    object_path: str
    blob_oid: str
    bytes_sha256: str
    raw_bytes: bytes = field(repr=False)
    fetch: GitCommandResult
    replacement_refs_before: list[str]
    repository_path: Path | None = field(default=None, repr=False)
    tip_probe_before: GitCommandResult | None = field(default=None, repr=False)
    tip_probe_after: GitCommandResult | None = field(default=None, repr=False)
    tip_oid_after: str | None = None
    replacement_refs_after: list[str] | None = None

    @property
    def stable(self) -> bool:
        return self.tip_oid_after == self.tip_oid_before and self.fetched_oid == self.tip_oid_before

    @property
    def replacement_refs_empty(self) -> bool:
        return self.replacement_refs_before == [] and self.replacement_refs_after == []

    def receipt_fields(self) -> dict[str, Any]:
        return {
            "remote_url": self.remote_url,
            "origin_slug": self.origin_slug,
            "refspec": self.refspec,
            "remote_tip_oid": self.tip_oid_before,
            "remote_tip_oid_before": self.tip_oid_before,
            "remote_tip_oid_after": self.tip_oid_after,
            "fetched_oid": self.fetched_oid,
            "tip_stable": self.stable,
            "tip_probe_before": (
                self.tip_probe_before.receipt_fields() if self.tip_probe_before else None
            ),
            "tip_probe_after": (
                self.tip_probe_after.receipt_fields() if self.tip_probe_after else None
            ),
            "fetch": self.fetch.receipt_fields(),
            "git_object_format": self.object_format,
            "object_spec": f"{self.tip_oid_before}:{self.object_path}",
            "object_path": self.object_path,
            "blob_oid": self.blob_oid,
            "bytes_sha256": self.bytes_sha256,
            "byte_read_count": 1,
            "same_bytes_used_for_parse_and_hash": True,
            "replacement_refs_before": self.replacement_refs_before,
            "replacement_refs_after": self.replacement_refs_after,
            "replacement_refs_empty": self.replacement_refs_empty,
        }


class BindingProvider(Protocol):
    def open(
        self,
        *,
        label: str,
        remote_url: str,
        expected_slug: str,
        refspec: str,
        object_path: str,
    ) -> RemoteBinding: ...

    def finalize(self, binding: RemoteBinding) -> None: ...


class GitBindingProvider:
    """Bind one canonical remote using an isolated temporary bare repository."""

    def __init__(self, root: Path, *, runner: GitRunner | None = None) -> None:
        self.root = root
        self.runner = runner or GitRunner()

    def _replacement_refs(self, repo: Path, *, label: str, partial: dict[str, Any]) -> list[str]:
        result = self.runner.run(
            ["-C", str(repo), "for-each-ref", "--format=%(refname)", "refs/replace/"],
        )
        output = _require_success(
            result,
            label=label,
            operation="replace_ref_check",
            partial=partial,
        )
        text = output.decode("utf-8", errors="strict")
        return [line for line in text.splitlines() if line]

    def _remote_tip(
        self,
        remote_url: str,
        refspec: str,
        *,
        label: str,
        partial: dict[str, Any],
        operation: str,
    ) -> tuple[str, GitCommandResult]:
        # A network probe must not discover configuration from the caller's
        # checkout. ``root`` is the private temporary parent of our bare repos,
        # not a Git repository.
        result = self.runner.run(
            ["ls-remote", "--exit-code", "--refs", remote_url, refspec],
            cwd=self.root,
        )
        output = _require_success(
            result,
            label=label,
            operation=operation,
            partial=partial,
        )
        try:
            lines = output.decode("ascii", errors="strict").splitlines()
        except UnicodeDecodeError as exc:
            raise BindingFailure(
                label=label,
                reason=f"{label}_{operation}_malformed",
                partial=partial,
            ) from exc
        matches: list[str] = []
        for line in lines:
            fields = line.split("\t")
            if len(fields) == 2 and fields[1] == refspec and _OID_RE.fullmatch(fields[0]):
                matches.append(fields[0])
        if len(matches) != 1:
            raise BindingFailure(
                label=label,
                reason=f"{label}_{operation}_expected_one_tip_found={len(matches)}",
                partial=partial,
            )
        return matches[0], result

    def open(
        self,
        *,
        label: str,
        remote_url: str,
        expected_slug: str,
        refspec: str,
        object_path: str,
    ) -> RemoteBinding:
        partial: dict[str, Any] = {
            "remote_url": remote_url,
            "expected_origin_slug": expected_slug,
            "refspec": refspec,
            "object_path": object_path,
        }
        actual_slug = canonical_slug(remote_url)
        partial["origin_slug"] = actual_slug
        if actual_slug is None:
            raise BindingFailure(
                label=label,
                reason=f"{label}_remote_url_not_canonical_github",
                partial=partial,
            )
        if actual_slug != expected_slug:
            raise BindingFailure(
                label=label,
                reason=(
                    f"{label}_origin_slug_mismatch=actual={actual_slug} expected={expected_slug}"
                ),
                partial=partial,
            )

        repo = self.root / f"{label}.git"
        init = self.runner.run(
            ["-c", "init.defaultBranch=main", "init", "--bare", str(repo)],
            cwd=self.root,
        )
        _require_success(init, label=label, operation="bare_init", partial=partial)
        replacement_refs_before = self._replacement_refs(repo, label=label, partial=partial)
        partial["replacement_refs_before"] = replacement_refs_before

        tip_before, tip_probe_before = self._remote_tip(
            remote_url,
            refspec,
            label=label,
            partial=partial,
            operation="ls_remote_before",
        )
        partial["remote_tip_oid_before"] = tip_before
        fetch = self.runner.run(
            [
                "-C",
                str(repo),
                "fetch",
                "--force",
                "--depth=1",
                "--no-tags",
                "--no-write-fetch-head",
                remote_url,
                f"+{refspec}:{LOCAL_FETCH_REF}",
            ]
        )
        partial["fetch"] = fetch.receipt_fields()
        _require_success(fetch, label=label, operation="fetch", partial=partial)

        resolved = self.runner.run(["-C", str(repo), "rev-parse", f"{LOCAL_FETCH_REF}^{{commit}}"])
        fetched_oid = _decode_ascii_line(
            _require_success(
                resolved,
                label=label,
                operation="resolve_fetched_ref",
                partial=partial,
            ),
            label=label,
            field_name="fetched_oid",
        )
        partial["fetched_oid"] = fetched_oid

        format_result = self.runner.run(["-C", str(repo), "rev-parse", "--show-object-format"])
        object_format = _decode_ascii_line(
            _require_success(
                format_result,
                label=label,
                operation="object_format",
                partial=partial,
            ),
            label=label,
            field_name="object_format",
        )
        partial["git_object_format"] = object_format

        blob_result = self.runner.run(
            ["-C", str(repo), "cat-file", "blob", f"{tip_before}:{object_path}"]
        )
        raw_bytes = _require_success(
            blob_result,
            label=label,
            operation="object_read",
            partial=partial,
        )
        blob_oid = _git_blob_oid(raw_bytes, object_format)
        return RemoteBinding(
            label=label,
            remote_url=remote_url,
            origin_slug=actual_slug,
            refspec=refspec,
            tip_oid_before=tip_before,
            fetched_oid=fetched_oid,
            object_format=object_format,
            object_path=object_path,
            blob_oid=blob_oid,
            bytes_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            raw_bytes=raw_bytes,
            fetch=fetch,
            replacement_refs_before=replacement_refs_before,
            repository_path=repo,
            tip_probe_before=tip_probe_before,
        )

    def finalize(self, binding: RemoteBinding) -> None:
        if binding.repository_path is None:
            raise ValueError("real Git binding has no repository path")
        partial = binding.receipt_fields()
        binding.tip_oid_after, binding.tip_probe_after = self._remote_tip(
            binding.remote_url,
            binding.refspec,
            label=binding.label,
            partial=partial,
            operation="ls_remote_after",
        )
        binding.replacement_refs_after = self._replacement_refs(
            binding.repository_path,
            label=binding.label,
            partial=partial,
        )


@dataclass(frozen=True)
class CompletionRequest:
    project_id: str
    deployment_task_id: str
    registry_origin_slug: str
    registry_origin_url: str
    exact_argv: list[str]


def request_reasons(request: CompletionRequest) -> list[str]:
    reasons: list[str] = []
    if not request.project_id or any(ord(char) < 32 for char in request.project_id):
        reasons.append("project_id_invalid")
    if not _TASK_ID_RE.fullmatch(request.deployment_task_id):
        reasons.append("deployment_task_id_invalid")
    if not _SLUG_RE.fullmatch(request.registry_origin_slug):
        reasons.append("registry_origin_slug_invalid")
    actual_slug = canonical_slug(request.registry_origin_url)
    if actual_slug is None:
        reasons.append("registry_origin_url_not_canonical_github")
    elif actual_slug != request.registry_origin_slug:
        reasons.append(
            "registry_origin_slug_mismatch="
            f"actual={actual_slug} expected={request.registry_origin_slug}"
        )
    return reasons


def _release_pattern_reasons(
    project: dict[str, Any],
    state: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    pattern_value = project.get("release_id_pattern")
    if not isinstance(pattern_value, str) or not pattern_value:
        return ["release_id_pattern_missing_for_completion"]
    try:
        pattern = re.compile(pattern_value)
    except (re.error, OverflowError) as exc:
        return [f"release_id_pattern_invalid={exc}"]

    baseline_value = project.get("deployed_baseline")
    deployed_value = state.get("deployed_version")
    baseline = baseline_value if isinstance(baseline_value, str) else ""
    deployed = deployed_value if isinstance(deployed_value, str) else ""
    baseline_valid = bool(baseline)
    deployed_valid = bool(deployed)
    if not baseline_valid:
        reasons.append("deployed_baseline_missing_or_empty")
    elif not pattern.fullmatch(baseline):
        reasons.append(f"deployed_baseline_pattern_mismatch={baseline} pattern={pattern_value}")
    if not deployed_valid:
        reasons.append("deployed_version_missing_or_empty")
    elif not pattern.fullmatch(deployed):
        reasons.append(f"deployed_version_pattern_mismatch={deployed} pattern={pattern_value}")
    if baseline_valid and deployed_valid and deployed != baseline:
        reasons.append(
            f"declared_state_drift=registry_baseline={baseline} product_deployed_version={deployed}"
        )

    if "pending_version" not in state:
        reasons.append("pending_version_absent")
    else:
        pending_value = state.get("pending_version")
        if not isinstance(pending_value, str) or not pending_value:
            reasons.append("pending_version_missing_or_empty")
        elif pending_value != "none":
            pending = pending_value
            if not pattern.fullmatch(pending):
                reasons.append(
                    f"pending_version_pattern_mismatch={pending} pattern={pattern_value}"
                )
            if deployed_valid and pending == deployed:
                reasons.append(f"pending_version_equals_deployed={pending}")
    return reasons


def _project_metadata_reasons(project: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for field_name in _PROJECT_STRING_FIELDS:
        value = project.get(field_name)
        if value is not None and not isinstance(value, str):
            reasons.append(f"registry_project_field_not_string={field_name}")
    tier = project.get("tier")
    if isinstance(tier, str) and tier not in {
        "governance",
        "infrastructure",
        "product",
        "content",
        "experimental",
        "enforcement",
    }:
        reasons.append(f"registry_project_tier_invalid={tier}")
    active_version_type = project.get("active_version_type")
    if isinstance(active_version_type, str) and active_version_type not in {
        "branch",
        "tag",
        "label",
    }:
        reasons.append(f"registry_project_active_version_type_invalid={active_version_type}")
    claim_required = project.get("claim_required")
    if claim_required is not None and not (
        isinstance(claim_required, bool) or claim_required in ("true", "false")
    ):
        reasons.append("registry_project_claim_required_invalid")
    return reasons


def _declared_state_payload(
    project: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    pattern_value = project.get("release_id_pattern")
    baseline = project.get("deployed_baseline")
    deployed = state.get("deployed_version")
    pending = state.get("pending_version")
    matches: dict[str, bool | None] = {
        "deployed_baseline": None,
        "deployed_version": None,
        "pending_version": None,
    }
    if isinstance(pattern_value, str) and pattern_value:
        try:
            pattern = re.compile(pattern_value)
        except (re.error, OverflowError):
            pass
        else:
            if isinstance(baseline, str) and baseline:
                matches["deployed_baseline"] = pattern.fullmatch(baseline) is not None
            if isinstance(deployed, str) and deployed:
                matches["deployed_version"] = pattern.fullmatch(deployed) is not None
            if pending == "none":
                matches["pending_version"] = True
            elif isinstance(pending, str) and pending:
                matches["pending_version"] = pattern.fullmatch(pending) is not None
    return {
        "version_contract": project.get("version_contract"),
        "release_id_pattern": pattern_value,
        "deployed_baseline": baseline,
        "deployed_version": deployed,
        "pending_version": pending,
        "pattern_method": "Python re.fullmatch",
        "pattern_fullmatch": matches,
        "declared_parity_equal": bool(
            isinstance(baseline, str)
            and baseline
            and isinstance(deployed, str)
            and deployed
            and baseline == deployed
        ),
    }


def _binding_reasons(binding: RemoteBinding) -> list[str]:
    reasons: list[str] = []
    if binding.fetched_oid != binding.tip_oid_before:
        reasons.append(
            f"{binding.label}_fetched_oid_stale="
            f"fetched={binding.fetched_oid} remote={binding.tip_oid_before}"
        )
    if binding.tip_oid_after != binding.tip_oid_before:
        reasons.append(
            f"{binding.label}_remote_tip_changed="
            f"before={binding.tip_oid_before} after={binding.tip_oid_after}"
        )
    if not binding.replacement_refs_empty:
        reasons.append(f"{binding.label}_replacement_refs_not_empty")
    return reasons


def _project_check_payload(
    *,
    project_id: str,
    project: dict[str, Any] | None,
    registry: RemoteBinding | None,
    product: RemoteBinding | None,
    reasons: list[str],
    dependency_blocked: bool,
) -> dict[str, Any]:
    ok = not reasons
    status = (
        "DEPENDENCY_BLOCKED"
        if dependency_blocked
        else "DECLARED_STATE_GUARD_OK"
        if ok
        else "PROJECT_CHECK_BLOCKED"
    )
    binding: dict[str, Any] = {
        "git_object_format": product.object_format if product else None,
        "working_tree_mode": False,
        "ref": "origin/main",
        "product_ref": "origin/main",
        "freshness": "fetched",
        "remote_tip_stability": "before-and-after-checked",
        "version_contract": project.get("version_contract") if project else None,
    }
    if registry is not None:
        binding.update(
            {
                "registry_head_oid": registry.tip_oid_before,
                "registry_projects_blob": registry.blob_oid,
                "registry_projects_sha256": registry.bytes_sha256,
                "registry_origin_slug": registry.origin_slug,
                "registry_source": f"{registry.tip_oid_before}:{REGISTRY_BLOB_PATH}",
            }
        )
    if product is not None:
        binding.update(
            {
                "origin_slug": product.origin_slug,
                "product_ref_oid": product.tip_oid_before,
                "version_state_blob": product.blob_oid,
                "version_state_sha256": product.bytes_sha256,
            }
        )
    project = project or {}

    def optional_string(field_name: str) -> str | None:
        value = project.get(field_name)
        return value if isinstance(value, str) else None

    return {
        "kind": "ProjectCheck",
        "schema_version": 1,
        "status": status,
        "project_id": project_id,
        "project_path": None,
        "repo": optional_string("repo"),
        "tier": optional_string("tier"),
        "active_version": optional_string("active_version"),
        "active_version_type": optional_string("active_version_type"),
        "deployed_baseline": optional_string("deployed_baseline"),
        "start_command": optional_string("start_command"),
        "finish_command": optional_string("finish_command"),
        "claim_required": str(project.get("claim_required", "false")).lower() == "true",
        "claim_command": optional_string("claim_command"),
        "cross_project_role": optional_string("cross_project_role"),
        "secrets_mode": optional_string("secrets_mode"),
        "input_binding": binding,
        "runtime": {
            "python_version": sys.version.split()[0],
            "minimum_python": "3.10",
            "yaml_parser": "strict_yaml (SafeLoader; implicit typing disabled)",
        },
        "ok": ok,
        "reasons": list(reasons),
        "pending_reasons": [],
    }


def _base_receipt(request: CompletionRequest) -> dict[str, Any]:
    return {
        "kind": "ProjectCompletionReceipt",
        "schema_version": 1,
        "profile": "declared-state-git-binding-v1",
        "status": COMPLETION_STATUS_BLOCKED,
        "ok": False,
        "completion_authorized": False,
        "project_id": request.project_id,
        "deployment_task_id": request.deployment_task_id,
        "generated_at_utc": None,
        "argv": list(request.exact_argv),
        "process": {
            "exit_code": 1,
            "observation": "self-reported intended return code",
        },
        "reasons": [],
        "verified_dimensions": [],
        "unverified_dimensions": list(UNVERIFIED_DIMENSIONS),
        "unverified_dimensions_non_exhaustive": True,
        "scope_notes": {
            "success_meaning": (
                "Declared registry/product Git bindings passed; this is not proof "
                "that a deployment instance caused a live state."
            ),
            "trusted_nonce": (
                "Out of scope until an authoritative issuer and single-use ledger "
                "define issuance, uniqueness, expiry, and verification."
            ),
            "monotonic_deployment_sequence": (
                "Out of scope until the deployment control plane and live readback "
                "expose one authoritative, serialized sequence."
            ),
        },
        "git_security": {
            "required_environment": {"GIT_NO_REPLACE_OBJECTS": "1"},
            "system_and_global_git_config_disabled": True,
            "isolated_temporary_bare_repositories": True,
        },
        "registry_binding": None,
        "product_binding": None,
        "declared_state": None,
        "project_check": None,
    }


def run_completion(
    request: CompletionRequest,
    *,
    provider: BindingProvider | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> tuple[dict[str, Any], int]:
    """Verify completion bindings and return one sealed receipt plus exit code."""

    receipt = _base_receipt(request)
    reasons = request_reasons(request)
    project: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    registry: RemoteBinding | None = None
    product: RemoteBinding | None = None
    dependency_blocked = False
    owned_temp: tempfile.TemporaryDirectory[str] | None = None
    if provider is None:
        owned_temp = tempfile.TemporaryDirectory(prefix="multiagent-completion-")
        provider = GitBindingProvider(Path(owned_temp.name), runner=GitRunner(clock=clock))

    try:
        if not reasons:
            try:
                registry = provider.open(
                    label="registry",
                    remote_url=request.registry_origin_url,
                    expected_slug=request.registry_origin_slug,
                    refspec=MAIN_REFSPEC,
                    object_path=REGISTRY_BLOB_PATH,
                )
                if registry.replacement_refs_before:
                    reasons.append("registry_replacement_refs_not_empty")
                projects = strict_yaml.load_strict_bytes(
                    registry.raw_bytes,
                    source=f"{registry.tip_oid_before}:{REGISTRY_BLOB_PATH}",
                    schema=lambda data: strict_yaml.parse_projects_registry(
                        data,
                        source=f"{registry.tip_oid_before}:{REGISTRY_BLOB_PATH}",
                    ),
                )
                project = projects.get(request.project_id)
                if project is None:
                    reasons.append(f"unknown_project={request.project_id}")
                elif project.get("version_contract") != "legacy-declared-parity":
                    reasons.append("completion_requires_version_contract=legacy-declared-parity")
                else:
                    reasons.extend(_project_metadata_reasons(project))
                    state_path = project.get("version_state")
                    if state_path != VERSION_STATE_PATH:
                        reasons.append(f"version_state_path_rebinding_forbidden={state_path}")
                    remote_url = project.get("repo")
                    if not isinstance(remote_url, str) or not remote_url:
                        reasons.append("registry_repo_field_missing")
                    else:
                        expected_product_slug = canonical_slug(remote_url)
                        if expected_product_slug is None:
                            reasons.append("registry_repo_url_not_canonical_github")
                        elif isinstance(project.get("repo_slug"), str) and (
                            project["repo_slug"] != expected_product_slug
                        ):
                            reasons.append(
                                "registry_repo_slug_mismatch="
                                f"url={expected_product_slug} field={project['repo_slug']}"
                            )
                        elif not reasons:
                            product = provider.open(
                                label="product",
                                remote_url=remote_url,
                                expected_slug=expected_product_slug,
                                refspec=MAIN_REFSPEC,
                                object_path=VERSION_STATE_PATH,
                            )
                            if product.replacement_refs_before:
                                reasons.append("product_replacement_refs_not_empty")
                            state = strict_yaml.load_strict_bytes(
                                product.raw_bytes,
                                source=f"{product.tip_oid_before}:{VERSION_STATE_PATH}",
                                schema=lambda data: strict_yaml.parse_flat_state(
                                    data,
                                    source=(f"{product.tip_oid_before}:{VERSION_STATE_PATH}"),
                                ),
                            )
                            if str(state.get("schema_version") or "") != "1":
                                reasons.append("version_state_schema_version_must_equal=1")
                            state_slug = state.get("expected_remote_slug")
                            if state_slug and state_slug != expected_product_slug:
                                reasons.append(
                                    "state_remote_slug_mismatch="
                                    f"actual={expected_product_slug} expected={state_slug}"
                                )
                            reasons.extend(_release_pattern_reasons(project, state))
            except BindingFailure as exc:
                reasons.append(exc.reason)
                receipt[f"{exc.label}_binding"] = exc.partial
            except strict_yaml.DependencyBlocked as exc:
                dependency_blocked = True
                reasons.append(str(exc))
            except strict_yaml.StrictYAMLError as exc:
                reasons.append(f"strict_yaml_error={exc}")
            except (OSError, ValueError) as exc:
                reasons.append(f"completion_input_error={exc}")
    finally:
        for binding in (product, registry):
            if binding is None:
                continue
            try:
                provider.finalize(binding)
            except BindingFailure as exc:
                reasons.append(exc.reason)
            except (OSError, ValueError) as exc:
                reasons.append(f"{binding.label}_finalization_failed={exc}")

        if registry is not None:
            reasons.extend(_binding_reasons(registry))
            receipt["registry_binding"] = registry.receipt_fields()
        if product is not None:
            reasons.extend(_binding_reasons(product))
            receipt["product_binding"] = product.receipt_fields()
        if owned_temp is not None:
            owned_temp.cleanup()

    # Preserve first occurrence order while eliminating duplicate diagnostics.
    reasons = list(dict.fromkeys(reasons))
    exit_code = 4 if dependency_blocked else 0 if not reasons else 1
    receipt["reasons"] = reasons
    receipt["ok"] = exit_code == 0
    receipt["status"] = (
        "DEPENDENCY_BLOCKED"
        if dependency_blocked
        else COMPLETION_STATUS_OK
        if exit_code == 0
        else COMPLETION_STATUS_BLOCKED
    )
    receipt["process"] = {
        "exit_code": exit_code,
        "observation": "self-reported intended return code",
    }
    receipt["verified_dimensions"] = list(VERIFIED_DIMENSIONS) if exit_code == 0 else []
    receipt["generated_at_utc"] = format_utc(clock())
    if project is not None and state is not None:
        receipt["declared_state"] = _declared_state_payload(project, state)
    receipt["project_check"] = _project_check_payload(
        project_id=request.project_id,
        project=project,
        registry=registry,
        product=product,
        reasons=reasons,
        dependency_blocked=dependency_blocked,
    )
    return seal_receipt(receipt), exit_code


def usage_failure_receipt(
    *,
    exact_argv: list[str],
    reason: str,
    project_id: str = "",
    deployment_task_id: str = "",
    clock: Callable[[], datetime] = utc_now,
) -> tuple[dict[str, Any], int]:
    request = CompletionRequest(
        project_id=project_id,
        deployment_task_id=deployment_task_id,
        registry_origin_slug="",
        registry_origin_url="",
        exact_argv=exact_argv,
    )
    receipt = _base_receipt(request)
    receipt["reasons"] = [reason]
    receipt["process"] = {
        "exit_code": 2,
        "observation": "self-reported intended return code",
    }
    receipt["generated_at_utc"] = format_utc(clock())
    receipt["project_check"] = _project_check_payload(
        project_id=project_id,
        project=None,
        registry=None,
        product=None,
        reasons=[reason],
        dependency_blocked=False,
    )
    return seal_receipt(receipt), 2
