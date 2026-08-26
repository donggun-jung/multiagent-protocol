"""Adversarial coverage for the exact-object completion subreceipt."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator, ValidationError

from multiagent_protocol import check_project
from multiagent_protocol import cli as protocol_cli
from multiagent_protocol.version_truth import completion as completion_module
from multiagent_protocol.version_truth.completion import (
    BindingFailure,
    CompletionRequest,
    GitBindingProvider,
    GitCommandResult,
    GitRunner,
    RemoteBinding,
    run_completion,
    verify_receipt_sha256,
)

REGISTRY_TIP = "a" * 40
PRODUCT_TIP = "b" * 40
FIXED_TIME = datetime(2026, 8, 26, 1, 2, 3, tzinfo=timezone.utc)
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "project_completion_receipt.schema.json"
)
SSH_REMOTE_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "version_truth" / "product_ssh_remote.txt"
)


def _assert_schema(receipt: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    Draft7Validator(schema).validate(receipt)


def _registry_bytes(
    *,
    baseline: str | None = "rel-7",
    pattern: str | None = r"^rel-[0-9]+$",
    version_state: str | None = "VERSION_STATE.yml",
    contract: str = "legacy-declared-parity",
) -> bytes:
    pattern_line = f'    release_id_pattern: "{pattern}"\n' if pattern is not None else ""
    state_line = f"    version_state: {version_state}\n" if version_state is not None else ""
    baseline_line = f"    deployed_baseline: {baseline}\n" if baseline is not None else ""
    return (
        "schema_version: 1\n"
        "projects:\n"
        "  - id: service-a\n"
        "    repo: https://github.com/example-org/service-a.git\n"
        "    repo_slug: example-org/service-a\n"
        f"    version_contract: {contract}\n"
        f"{state_line}"
        "    active_version: service-active\n"
        "    active_version_type: label\n"
        f"{baseline_line}"
        f"{pattern_line}"
        "    tier: product\n"
    ).encode()


def _state_bytes(
    *,
    deployed: str | None = "rel-7",
    pending: str | None = "none",
) -> bytes:
    pending_line = f"pending_version: {pending}\n" if pending is not None else ""
    deployed_line = f"deployed_version: {deployed}\n" if deployed is not None else ""
    return (
        "schema_version: 1\n"
        "expected_remote_slug: example-org/service-a\n"
        "active_version: service-active\n"
        f"{deployed_line}"
        f"{pending_line}"
    ).encode()


def _blob_oid(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()  # noqa: S324


def _fetch_result(label: str) -> GitCommandResult:
    return GitCommandResult(
        argv=["git", "fetch", label],
        started_at_utc="2026-08-26T01:02:01.000Z",
        completed_at_utc="2026-08-26T01:02:02.000Z",
        exit_code=0,
        stdout=b"",
        stderr=b"",
    )


def _probe_result(label: str, phase: str) -> GitCommandResult:
    return GitCommandResult(
        argv=["git", "ls-remote", label, phase],
        started_at_utc="2026-08-26T01:02:01.000Z",
        completed_at_utc="2026-08-26T01:02:02.000Z",
        exit_code=0,
        stdout=b"",
        stderr=b"",
    )


class FakeBindingProvider:
    def __init__(
        self,
        *,
        registry_bytes: bytes | None = None,
        product_bytes: bytes | None = None,
        fetched: dict[str, str] | None = None,
        after: dict[str, str] | None = None,
        replacement_before: dict[str, list[str]] | None = None,
        replacement_after: dict[str, list[str]] | None = None,
    ) -> None:
        self.raw = {
            "registry": registry_bytes or _registry_bytes(),
            "product": product_bytes or _state_bytes(),
        }
        self.tips = {"registry": REGISTRY_TIP, "product": PRODUCT_TIP}
        self.fetched = fetched or {}
        self.after = after or {}
        self.replacement_before = replacement_before or {}
        self.replacement_after = replacement_after or {}
        self.opened: list[str] = []
        self.opened_urls: dict[str, str] = {}

    def open(
        self,
        *,
        label: str,
        remote_url: str,
        expected_slug: str,
        refspec: str,
        object_path: str,
    ) -> RemoteBinding:
        self.opened.append(label)
        self.opened_urls[label] = remote_url
        raw = self.raw[label]
        expected_paths = {
            "registry": "governance/projects.yml",
            "product": "VERSION_STATE.yml",
        }
        assert object_path == expected_paths[label]
        assert refspec == "refs/heads/main"
        assert expected_slug in remote_url
        return RemoteBinding(
            label=label,
            remote_url=remote_url,
            origin_slug=expected_slug,
            refspec=refspec,
            tip_oid_before=self.tips[label],
            fetched_oid=self.fetched.get(label, self.tips[label]),
            object_format="sha1",
            object_path=object_path,
            blob_oid=_blob_oid(raw),
            bytes_sha256=hashlib.sha256(raw).hexdigest(),
            raw_bytes=raw,
            fetch=_fetch_result(label),
            replacement_refs_before=self.replacement_before.get(label, []),
            tip_probe_before=_probe_result(label, "before"),
        )

    def finalize(self, binding: RemoteBinding) -> None:
        binding.tip_oid_after = self.after.get(binding.label, binding.tip_oid_before)
        binding.replacement_refs_after = self.replacement_after.get(binding.label, [])
        binding.tip_probe_after = _probe_result(binding.label, "after")


class FinalProbeFailureProvider(FakeBindingProvider):
    def __init__(self, fail_label: str = "product") -> None:
        super().__init__()
        self.fail_label = fail_label

    def finalize(self, binding: RemoteBinding) -> None:
        if binding.label == self.fail_label:
            failed_probe = _probe_result(binding.label, "after")
            failed_probe = GitCommandResult(
                argv=failed_probe.argv,
                started_at_utc=failed_probe.started_at_utc,
                completed_at_utc=failed_probe.completed_at_utc,
                exit_code=128,
                stdout=b"",
                stderr=b"",
            )
            binding.tip_probe_after = failed_probe
            raise BindingFailure(
                label=binding.label,
                reason=f"{binding.label}_ls_remote_after_failed_exit=128",
                partial=binding.receipt_fields(),
                command_result=failed_probe,
            )
        super().finalize(binding)


class PartialProductFailureProvider(FakeBindingProvider):
    def __init__(
        self,
        *,
        before_exit: int = 0,
        fetch_exit: int = 0,
        include_valid_tip: bool = True,
    ) -> None:
        super().__init__()
        self.before_exit = before_exit
        self.fetch_exit = fetch_exit
        self.include_valid_tip = include_valid_tip

    def open(
        self,
        *,
        label: str,
        remote_url: str,
        expected_slug: str,
        refspec: str,
        object_path: str,
    ) -> RemoteBinding:
        if label == "registry":
            return super().open(
                label=label,
                remote_url=remote_url,
                expected_slug=expected_slug,
                refspec=refspec,
                object_path=object_path,
            )
        self.opened.append(label)
        before = _probe_result(label, "before")
        before = GitCommandResult(
            argv=before.argv,
            started_at_utc=before.started_at_utc,
            completed_at_utc=before.completed_at_utc,
            exit_code=self.before_exit,
            stdout=b"",
            stderr=b"",
        )
        fetch = _fetch_result(label)
        fetch = GitCommandResult(
            argv=fetch.argv,
            started_at_utc=fetch.started_at_utc,
            completed_at_utc=fetch.completed_at_utc,
            exit_code=self.fetch_exit,
            stdout=b"",
            stderr=b"",
        )
        partial = {
            "tip_probe_before": before.receipt_fields(),
            "fetch": fetch.receipt_fields(),
        }
        if self.include_valid_tip:
            partial["remote_tip_oid_before"] = PRODUCT_TIP
        raise BindingFailure(
            label="product",
            reason="product_resolve_fetched_ref_failed_exit=128",
            partial=partial,
        )


class ProbeRunner(GitRunner):
    def __init__(self) -> None:
        super().__init__(clock=lambda: FIXED_TIME)
        self.observed_cwd: Path | None = None

    def run(self, args: list[str], *, cwd: Path | None = None) -> GitCommandResult:
        self.observed_cwd = cwd
        return GitCommandResult(
            argv=["git", *args],
            started_at_utc="2026-08-26T01:02:01.000Z",
            completed_at_utc="2026-08-26T01:02:02.000Z",
            exit_code=0,
            stdout=f"{REGISTRY_TIP}\trefs/heads/main\n".encode(),
            stderr=b"",
        )


class AuthenticatedGitHTTPHandler(BaseHTTPRequestHandler):
    """Serve ``git http-backend`` behind deterministic Basic authentication."""

    project_root: Path
    expected_authorization = "Basic " + base64.b64encode(b"user:pass").decode("ascii")

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def _authenticate(self) -> bool:
        if self.headers.get("Authorization") == self.expected_authorization:
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="completion-test"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _serve_git(self) -> None:
        if not self._authenticate():
            return
        path, _, query = self.path.partition("?")
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else b""
        env = dict(os.environ)
        env.update(
            {
                "GIT_PROJECT_ROOT": str(self.project_root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(content_length),
                "REMOTE_USER": "user",
            }
        )
        result = subprocess.run(
            ["git", "http-backend"],
            input=body,
            env=env,
            capture_output=True,
            check=False,
        )
        separator = b"\r\n\r\n" if b"\r\n\r\n" in result.stdout else b"\n\n"
        headers, response_body = result.stdout.split(separator, 1)
        status = 200
        response_headers: list[tuple[str, str]] = []
        for raw_line in headers.splitlines():
            name, value = raw_line.decode("latin-1").split(":", 1)
            if name.lower() == "status":
                status = int(value.strip().split()[0])
            else:
                response_headers.append((name, value.strip()))
        self.send_response(status)
        for name, value in response_headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    do_GET = _serve_git
    do_POST = _serve_git


def _request() -> CompletionRequest:
    return CompletionRequest(
        project_id="service-a",
        deployment_task_id="deploy-532",
        registry_origin_slug="example-org/company-operations",
        registry_origin_url="https://github.com/example-org/company-operations.git",
        exact_argv=[
            "multiagent-protocol",
            "check-project",
            "service-a",
            "--completion",
        ],
    )


def _run(provider: FakeBindingProvider) -> tuple[dict[str, Any], int]:
    return run_completion(_request(), provider=provider, clock=lambda: FIXED_TIME)


def test_valid_receipt_binds_both_exact_blobs_and_preserves_project_check_fields():
    receipt, exit_code = _run(FakeBindingProvider())

    assert exit_code == 0
    assert receipt["status"] == "DECLARED_STATE_COMPLETION_SUBRECEIPT_OK"
    assert receipt["ok"] is True
    assert receipt["completion_authorized"] is False
    assert receipt["deployment_task_id"] == "deploy-532"
    assert receipt["generated_at_utc"] == "2026-08-26T01:02:03.000Z"
    assert receipt["argv"][1] == "check-project"
    assert receipt["process"]["exit_code"] == 0
    assert verify_receipt_sha256(receipt)

    registry = receipt["registry_binding"]
    product = receipt["product_binding"]
    assert registry["remote_tip_oid"] == REGISTRY_TIP
    assert registry["transport_url"] == registry["remote_url"]
    assert registry["injected_git_config_args"] == []
    assert registry["object_spec"] == f"{REGISTRY_TIP}:governance/projects.yml"
    assert registry["byte_read_count"] == 1
    assert registry["same_bytes_used_for_parse_and_hash"] is True
    assert registry["tip_probe_before"]["exit_code"] == 0
    assert product["remote_tip_oid_before"] == product["remote_tip_oid_after"]
    assert product["object_spec"] == f"{PRODUCT_TIP}:VERSION_STATE.yml"
    assert product["replacement_refs_empty"] is True
    assert product["tip_probe_after"]["exit_code"] == 0

    declared = receipt["declared_state"]
    assert declared["deployed_baseline"] == "rel-7"
    assert declared["deployed_version"] == "rel-7"
    assert declared["pending_version"] == "none"
    assert declared["pattern_fullmatch"] == {
        "deployed_baseline": True,
        "deployed_version": True,
        "pending_version": None,
    }

    project_check = receipt["project_check"]
    assert project_check["kind"] == "ProjectCheck"
    assert project_check["status"] == "DECLARED_STATE_GUARD_OK"
    assert project_check["input_binding"]["freshness"] == "fetched"
    assert project_check["input_binding"]["remote_tip_stability"] == "before-and-after-checked"
    assert project_check["input_binding"]["product_ref_oid"] == PRODUCT_TIP
    assert project_check["input_binding"]["version_state_blob"] == _blob_oid(_state_bytes())
    assert "trusted_nonce_authenticity_and_uniqueness" in receipt["unverified_dimensions"]
    assert "authoritative_monotonic_deployment_sequence" in receipt["unverified_dimensions"]
    assert (
        "registry_origin_main_tip_stability_and_exact_blob_bytes" in receipt["verified_dimensions"]
    )
    assert receipt["git_security"]["required_environment"] == {
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert receipt["unverified_dimensions_non_exhaustive"] is True
    _assert_schema(receipt)


def test_success_schema_binds_pending_pattern_evaluation_to_the_sentinel():
    sentinel_receipt, sentinel_exit = _run(FakeBindingProvider())
    assert sentinel_exit == 0
    sentinel_receipt["declared_state"]["pattern_fullmatch"]["pending_version"] = True
    with pytest.raises(ValidationError):
        _assert_schema(sentinel_receipt)

    pending_receipt, pending_exit = _run(
        FakeBindingProvider(product_bytes=_state_bytes(pending="rel-8"))
    )
    assert pending_exit == 0
    assert pending_receipt["declared_state"]["pattern_fullmatch"]["pending_version"] is True
    _assert_schema(pending_receipt)
    pending_receipt["declared_state"]["pattern_fullmatch"]["pending_version"] = None
    with pytest.raises(ValidationError):
        _assert_schema(pending_receipt)


@pytest.mark.parametrize("label", ["registry", "product"])
def test_tip_change_between_probes_fails_toctou(label: str):
    provider = FakeBindingProvider(after={label: "c" * 40})

    receipt, exit_code = _run(provider)

    assert exit_code == 1
    assert any(f"{label}_remote_tip_changed" in reason for reason in receipt["reasons"])
    assert receipt[f"{label}_binding"]["tip_stable"] is False
    assert receipt["verified_dimensions"] == []
    _assert_schema(receipt)


@pytest.mark.parametrize("label", ["registry", "product"])
def test_finalize_probe_failure_is_distinct_and_does_not_invent_tip_change(label: str):
    receipt, exit_code = _run(FinalProbeFailureProvider(label))

    assert exit_code == 1
    assert f"{label}_finalize_probe_failed" in receipt["reasons"]
    assert f"{label}_ls_remote_after_failed_exit=128" in receipt["reasons"]
    assert not any(f"{label}_remote_tip_changed" in reason for reason in receipt["reasons"])
    assert f"{label}_replacement_refs_not_empty" not in receipt["reasons"]
    assert receipt[f"{label}_binding"]["tip_probe_after"]["exit_code"] == 128
    assert receipt[f"{label}_binding"]["remote_tip_oid_after"] is None
    assert receipt["project_check"]["input_binding"]["freshness"] == "fetched"
    expected_stability = "before-only" if label == "product" else "before-and-after-checked"
    assert receipt["project_check"]["input_binding"]["remote_tip_stability"] == expected_stability
    assert verify_receipt_sha256(receipt)
    _assert_schema(receipt)


def test_git_provider_preserves_failed_final_probe_command_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    provider = GitBindingProvider(tmp_path, runner=ProbeRunner())
    binding = FakeBindingProvider().open(
        label="product",
        remote_url="https://github.com/example-org/service-a.git",
        expected_slug="example-org/service-a",
        refspec="refs/heads/main",
        object_path="VERSION_STATE.yml",
    )
    binding.repository_path = tmp_path
    failed_probe = GitCommandResult(
        argv=["git", "ls-remote", "product", "after"],
        started_at_utc="2026-08-26T01:02:01.000Z",
        completed_at_utc="2026-08-26T01:02:02.000Z",
        exit_code=128,
        stdout=b"",
        stderr=b"",
    )

    def fail_final_probe(*_args, **kwargs):
        partial = kwargs["partial"]
        partial["tip_probe_after"] = failed_probe.receipt_fields()
        raise BindingFailure(
            label="product",
            reason="product_ls_remote_after_failed_exit=128",
            partial=partial,
            command_result=failed_probe,
        )

    monkeypatch.setattr(provider, "_remote_tip", fail_final_probe)

    with pytest.raises(BindingFailure, match="product_ls_remote_after_failed_exit=128"):
        provider.finalize(binding)

    assert binding.tip_oid_after is None
    assert binding.tip_probe_after is failed_probe


def test_partial_product_observation_derives_freshness_and_probe_state_from_exit_codes():
    fetched_receipt, fetched_exit = _run(PartialProductFailureProvider())

    assert fetched_exit == 1
    assert fetched_receipt["project_check"]["input_binding"]["freshness"] == "fetched"
    assert (
        fetched_receipt["project_check"]["input_binding"]["remote_tip_stability"] == "before-only"
    )

    failed_receipt, failed_exit = _run(
        PartialProductFailureProvider(before_exit=128, fetch_exit=128)
    )

    assert failed_exit == 1
    assert failed_receipt["project_check"]["input_binding"]["freshness"] is None
    assert failed_receipt["project_check"]["input_binding"]["remote_tip_stability"] == "not-probed"

    malformed_receipt, malformed_exit = _run(
        PartialProductFailureProvider(
            before_exit=0,
            fetch_exit=128,
            include_valid_tip=False,
        )
    )

    assert malformed_exit == 1
    assert malformed_receipt["project_check"]["input_binding"]["freshness"] is None
    assert (
        malformed_receipt["project_check"]["input_binding"]["remote_tip_stability"] == "not-probed"
    )


@pytest.mark.parametrize("label", ["registry", "product"])
def test_fetched_oid_must_equal_remote_tip_not_a_stale_ref(label: str):
    provider = FakeBindingProvider(fetched={label: "d" * 40})

    receipt, exit_code = _run(provider)

    assert exit_code == 1
    assert any(f"{label}_fetched_oid_stale" in reason for reason in receipt["reasons"])


@pytest.mark.parametrize("version_state", [None, "state/ALT.yml"])
def test_missing_or_alternate_version_state_path_is_rejected_before_product_read(
    version_state: str | None,
):
    provider = FakeBindingProvider(registry_bytes=_registry_bytes(version_state=version_state))

    receipt, exit_code = _run(provider)

    assert exit_code == 1
    assert f"version_state_path_rebinding_forbidden={version_state}" in receipt["reasons"]
    assert provider.opened == ["registry"]


@pytest.mark.parametrize(
    ("registry_bytes", "product_bytes", "reason_prefix"),
    [
        (_registry_bytes(baseline="build-7"), _state_bytes(), "deployed_baseline_pattern_mismatch"),
        (_registry_bytes(), _state_bytes(deployed="build-7"), "deployed_version_pattern_mismatch"),
        (_registry_bytes(), _state_bytes(pending="build-8"), "pending_version_pattern_mismatch"),
    ],
)
def test_release_pattern_fullmatches_all_three_identifier_fields(
    registry_bytes: bytes,
    product_bytes: bytes,
    reason_prefix: str,
):
    receipt, exit_code = _run(
        FakeBindingProvider(registry_bytes=registry_bytes, product_bytes=product_bytes)
    )

    assert exit_code == 1
    assert any(reason.startswith(reason_prefix) for reason in receipt["reasons"])


@pytest.mark.parametrize("pattern", ["[unterminated", "a{999999999999999999999999}"])
def test_invalid_registered_pattern_is_structured_failure_not_traceback(pattern: str):
    receipt, exit_code = _run(FakeBindingProvider(registry_bytes=_registry_bytes(pattern=pattern)))

    assert exit_code == 1
    assert any(reason.startswith("release_id_pattern_invalid=") for reason in receipt["reasons"])
    assert verify_receipt_sha256(receipt)


@pytest.mark.parametrize("pattern", [None, "", "   "])
def test_completion_requires_a_nonblank_registered_pattern(pattern: str | None):
    receipt, exit_code = _run(FakeBindingProvider(registry_bytes=_registry_bytes(pattern=pattern)))

    assert exit_code == 1
    assert "release_id_pattern_missing_for_completion" in receipt["reasons"]


def test_completion_applies_the_shared_release_pattern_length_cap():
    receipt, exit_code = _run(
        FakeBindingProvider(registry_bytes=_registry_bytes(pattern="a" * 1025))
    )

    assert exit_code == 1
    assert (
        "release_id_pattern_invalid=release_id_pattern exceeds 1024 characters"
        in receipt["reasons"]
    )


def test_permissive_regex_cannot_make_missing_identifiers_green():
    receipt, exit_code = _run(
        FakeBindingProvider(
            registry_bytes=_registry_bytes(baseline=None, pattern=r"^.*$"),
            product_bytes=_state_bytes(deployed=None),
        )
    )

    assert exit_code == 1
    assert "deployed_baseline_missing_or_empty" in receipt["reasons"]
    assert "deployed_version_missing_or_empty" in receipt["reasons"]
    assert receipt["declared_state"]["deployed_baseline"] is None
    assert receipt["declared_state"]["deployed_version"] is None
    assert receipt["declared_state"]["pattern_fullmatch"]["deployed_baseline"] is None
    assert receipt["declared_state"]["pattern_fullmatch"]["deployed_version"] is None
    assert receipt["declared_state"]["declared_parity_equal"] is False
    assert receipt["verified_dimensions"] == []


def test_registry_url_must_match_the_caller_supplied_canonical_slug():
    provider = FakeBindingProvider()
    request = CompletionRequest(
        project_id="service-a",
        deployment_task_id="deploy-532",
        registry_origin_slug="example-org/company-operations",
        registry_origin_url="https://github.com/attacker-org/company-operations.git",
        exact_argv=["multiagent-protocol", "check-project", "service-a"],
    )

    receipt, exit_code = run_completion(
        request,
        provider=provider,
        clock=lambda: FIXED_TIME,
    )

    assert exit_code == 1
    assert any("registry_origin_slug_mismatch" in reason for reason in receipt["reasons"])
    assert provider.opened == []


def test_registry_product_slug_field_cannot_rebind_the_repo_url():
    registry = _registry_bytes().replace(
        b"repo_slug: example-org/service-a",
        b"repo_slug: attacker-org/service-a",
    )
    provider = FakeBindingProvider(registry_bytes=registry)

    receipt, exit_code = _run(provider)

    assert exit_code == 1
    assert any("registry_repo_slug_mismatch" in reason for reason in receipt["reasons"])
    assert provider.opened == ["registry"]


def test_product_transport_override_must_preserve_the_registry_repo_slug():
    registered_transport = SSH_REMOTE_FIXTURE.read_text(encoding="utf-8").strip().encode()
    registry = _registry_bytes().replace(
        b"https://github.com/example-org/service-a.git",
        registered_transport,
    )
    provider = FakeBindingProvider(registry_bytes=registry)
    request = CompletionRequest(
        project_id="service-a",
        deployment_task_id="deploy-532",
        registry_origin_slug="example-org/company-operations",
        registry_origin_url="https://github.com/example-org/company-operations.git",
        exact_argv=["multiagent-protocol", "check-project", "service-a"],
        product_origin_url="https://github.com/example-org/service-a.git",
    )

    receipt, exit_code = run_completion(request, provider=provider, clock=lambda: FIXED_TIME)

    assert exit_code == 0
    assert provider.opened_urls["product"] == request.product_origin_url
    assert receipt["product_binding"]["transport_url"] == request.product_origin_url


def test_product_transport_override_cannot_rebind_to_another_slug():
    provider = FakeBindingProvider()
    request = CompletionRequest(
        project_id="service-a",
        deployment_task_id="deploy-532",
        registry_origin_slug="example-org/company-operations",
        registry_origin_url="https://github.com/example-org/company-operations.git",
        exact_argv=["multiagent-protocol", "check-project", "service-a"],
        product_origin_url="https://github.com/attacker-org/service-a.git",
    )

    receipt, exit_code = run_completion(request, provider=provider, clock=lambda: FIXED_TIME)

    assert exit_code == 1
    assert receipt["reasons"] == [
        "product_origin_slug_mismatch=actual=attacker-org/service-a expected=example-org/service-a"
    ]
    assert provider.opened == ["registry"]


def test_product_transport_override_rejects_embedded_credentials_before_network():
    provider = FakeBindingProvider()
    request = CompletionRequest(
        project_id="service-a",
        deployment_task_id="deploy-532",
        registry_origin_slug="example-org/company-operations",
        registry_origin_url="https://github.com/example-org/company-operations.git",
        exact_argv=["multiagent-protocol", "check-project", "service-a"],
        product_origin_url=(
            "https://github.com/example-org/service-a.git?access_token=placeholder"
        ),
    )

    receipt, exit_code = run_completion(request, provider=provider, clock=lambda: FIXED_TIME)

    assert exit_code == 1
    assert receipt["reasons"] == ["product_origin_url_not_canonical_github"]
    assert provider.opened == []


@pytest.mark.parametrize("helper", ["", "   ", "helper\ncommand"])
def test_credential_helper_must_be_nonblank_and_auditable(helper: str):
    provider = FakeBindingProvider()
    request = CompletionRequest(
        project_id="service-a",
        deployment_task_id="deploy-532",
        registry_origin_slug="example-org/company-operations",
        registry_origin_url="https://github.com/example-org/company-operations.git",
        exact_argv=["multiagent-protocol", "check-project", "service-a"],
        git_credential_helper=helper,
    )

    receipt, exit_code = run_completion(request, provider=provider, clock=lambda: FIXED_TIME)

    assert exit_code == 1
    assert receipt["reasons"] == ["git_credential_helper_invalid"]
    assert provider.opened == []


def test_malformed_legacy_metadata_cannot_create_schema_invalid_green_receipt():
    registry = _registry_bytes().replace(
        b"    tier: product\n",
        b"    tier:\n      - product\n",
    )

    receipt, exit_code = _run(FakeBindingProvider(registry_bytes=registry))

    assert exit_code == 1
    assert "registry_project_field_not_string=tier" in receipt["reasons"]
    assert receipt["project_check"]["tier"] is None
    _assert_schema(receipt)


def test_product_state_expected_slug_must_match_the_pinned_registry_remote():
    state = _state_bytes().replace(
        b"expected_remote_slug: example-org/service-a",
        b"expected_remote_slug: attacker-org/service-a",
    )

    receipt, exit_code = _run(FakeBindingProvider(product_bytes=state))

    assert exit_code == 1
    assert any("state_remote_slug_mismatch" in reason for reason in receipt["reasons"])


@pytest.mark.parametrize("label", ["registry", "product"])
def test_nonempty_replace_refs_fail_even_though_replacements_are_disabled(label: str):
    provider = FakeBindingProvider(replacement_before={label: ["refs/replace/" + "e" * 40]})

    receipt, exit_code = _run(provider)

    assert exit_code == 1
    assert f"{label}_replacement_refs_not_empty" in receipt["reasons"]


@pytest.mark.parametrize("label", ["registry", "product"])
def test_replace_ref_injected_after_read_is_detected(label: str):
    provider = FakeBindingProvider(replacement_after={label: ["refs/replace/" + "e" * 40]})

    receipt, exit_code = _run(provider)

    assert exit_code == 1
    assert f"{label}_replacement_refs_not_empty" in receipt["reasons"]
    assert receipt[f"{label}_binding"]["replacement_refs_after"]


def test_raw_trailing_newline_is_part_of_bound_sha256():
    state = _state_bytes() + b"\n"
    receipt, exit_code = _run(FakeBindingProvider(product_bytes=state))

    assert exit_code == 0
    assert receipt["product_binding"]["bytes_sha256"] == hashlib.sha256(state).hexdigest()


def test_non_utf8_state_fails_closed_with_a_sealed_receipt():
    receipt, exit_code = _run(FakeBindingProvider(product_bytes=b"\xff"))

    assert exit_code == 1
    assert any("not valid UTF-8" in reason for reason in receipt["reasons"])
    assert verify_receipt_sha256(receipt)


def test_receipt_self_hash_detects_mutation():
    receipt, _ = _run(FakeBindingProvider())
    receipt["deployment_task_id"] = "changed"

    assert verify_receipt_sha256(receipt) is False


@pytest.mark.parametrize(
    ("option", "reason"),
    [
        (["--registry", "fixture.yml"], "completion_rejects_registry_path_override"),
        (["--registry-repo", "."], "completion_rejects_working_tree_registry_repo"),
        (["--path", "."], "completion_rejects_product_working_tree_path"),
        (["--base-dir", "."], "completion_rejects_product_base_dir"),
        (["--allow-working-tree"], "completion_rejects_allow_working_tree"),
        (["--skip-fetch"], "completion_rejects_skip_fetch"),
        (["--ref", "origin/dev"], "completion_requires_ref=origin/main"),
    ],
)
def test_completion_rejects_mutable_legacy_inputs_as_one_json_receipt(
    option: list[str],
    reason: str,
    capsys: pytest.CaptureFixture[str],
):
    argv = [
        "service-a",
        "--completion",
        "--deployment-task-id",
        "deploy-532",
        "--registry-origin-slug",
        "example-org/company-operations",
        *option,
    ]
    exact = ["multiagent-protocol", "check-project", *argv]

    exit_code = check_project.main(argv, exact_argv=exact)
    captured = capsys.readouterr()
    lines = captured.out.splitlines()

    assert exit_code == 2
    assert captured.err == ""
    assert len(lines) == 1
    receipt = json.loads(lines[0])
    assert reason in receipt["reasons"][0]
    assert receipt["argv"] == exact
    assert receipt["process"]["exit_code"] == 2
    assert verify_receipt_sha256(receipt)
    _assert_schema(receipt)


@pytest.mark.parametrize(
    "global_options",
    [
        ["--log-level", "DEBUG"],
        ["--log-level=DEBUG"],
    ],
)
def test_global_log_level_before_check_project_preserves_json_receipt_dispatch(
    global_options: list[str],
    capsys: pytest.CaptureFixture[str],
):
    raw_argv = [*global_options, "check-project", "service-a", "--completion"]

    exit_code = protocol_cli.main(raw_argv)
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == ""
    receipt = json.loads(captured.out)
    assert receipt["argv"][1:] == raw_argv
    assert receipt["project_check"]["input_binding"]["freshness"] is None
    assert receipt["project_check"]["input_binding"]["remote_tip_stability"] == "not-probed"
    assert verify_receipt_sha256(receipt)
    _assert_schema(receipt)


def test_malformed_global_log_level_is_not_silently_dispatched_to_check_project(
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit) as exc_info:
        protocol_cli.main(["--log-level", "--bad", "check-project"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "--log-level" in captured.err


def test_global_log_level_before_check_registry_reaches_the_specialized_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
):
    from multiagent_protocol import check_registry

    observed: list[str] = []
    monkeypatch.setattr(
        check_registry,
        "main",
        lambda argv: observed.extend(argv) or 17,
    )

    exit_code = protocol_cli.main(
        ["--log-level", "DEBUG", "check-registry", "--baseline-ref", "a" * 40]
    )

    assert exit_code == 17
    assert observed == ["--baseline-ref", "a" * 40]


def test_git_runner_forces_no_replace_and_scrubs_topology_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_env: dict[str, str] = {}

    def fake_run(argv, **kwargs):
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("GIT_DIR", "/tmp/attacker")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/tmp/objects")
    monkeypatch.setenv(
        "GIT_CONFIG_PARAMETERS",
        "'url.file:///tmp/attacker.insteadOf=https://github.com/'",
    )
    monkeypatch.setenv("GIT_SSH_COMMAND", "/tmp/attacker-ssh")

    result = GitRunner(clock=lambda: FIXED_TIME).run(["version"])

    assert result.exit_code == 0
    assert captured_env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert "GIT_DIR" not in captured_env
    assert "GIT_OBJECT_DIRECTORY" not in captured_env
    assert "GIT_CONFIG_PARAMETERS" not in captured_env
    assert "GIT_SSH_COMMAND" not in captured_env
    assert captured_env["GIT_CONFIG_GLOBAL"]
    assert captured_env["GIT_CONFIG_SYSTEM"]


def test_git_runner_injects_only_the_explicit_credential_helper(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_argv: list[str] = []

    def fake_run(argv, **_kwargs):
        captured_argv.extend(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    helper = "!gh auth git-credential"

    GitRunner(clock=lambda: FIXED_TIME, credential_helper=helper).run(["version"])

    assert captured_argv == ["git", "-c", f"credential.helper={helper}", "version"]


def test_remote_tip_probe_uses_the_private_neutral_directory(tmp_path: Path):
    root = tmp_path / "neutral"
    root.mkdir()
    runner = ProbeRunner()
    provider = GitBindingProvider(root, runner=runner)

    tip, _probe = provider._remote_tip(
        "https://github.com/example-org/service-a.git",
        "refs/heads/main",
        label="product",
        partial={},
        operation="ls_remote_before",
    )

    assert tip == REGISTRY_TIP
    assert runner.observed_cwd == root


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_real_git_replace_is_both_disabled_and_detectable(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    state = repo / "VERSION_STATE.yml"
    state.write_text("deployed_version: rel-1\n", encoding="utf-8")
    _git(repo, "add", "VERSION_STATE.yml")
    _git(repo, "commit", "-q", "-m", "original")
    original = _git(repo, "rev-parse", "HEAD")
    state.write_text("deployed_version: rel-2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "replacement")
    replacement = _git(repo, "rev-parse", "HEAD")
    _git(repo, "replace", original, replacement)

    runner = GitRunner(clock=lambda: FIXED_TIME)
    shown = runner.run(["-C", str(repo), "show", f"{original}:VERSION_STATE.yml"])
    refs = runner.run(["-C", str(repo), "for-each-ref", "--format=%(refname)", "refs/replace/"])

    assert shown.stdout == b"deployed_version: rel-1\n"
    assert refs.stdout.decode().strip() == f"refs/replace/{original}"


def _local_remote(tmp_path: Path, data: bytes) -> tuple[Path, Path]:
    work = tmp_path / "work"
    remote = tmp_path / "remote.git"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.name", "Test User")
    _git(work, "config", "user.email", "test@example.com")
    (work / "VERSION_STATE.yml").write_bytes(data)
    _git(work, "add", "VERSION_STATE.yml")
    _git(work, "commit", "-q", "-m", "initial")
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "-u", "origin", "main")
    return work, remote


def test_real_binding_reads_the_remote_oid_not_mutable_working_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    original_bytes = _state_bytes()
    work, remote = _local_remote(tmp_path, original_bytes)
    (work / "VERSION_STATE.yml").write_bytes(_state_bytes(deployed="rel-99"))
    monkeypatch.setattr(
        completion_module,
        "canonical_slug",
        lambda _url: "example-org/service-a",
    )
    bindings = tmp_path / "bindings"
    bindings.mkdir()
    provider = GitBindingProvider(bindings, runner=GitRunner(clock=lambda: FIXED_TIME))

    binding = provider.open(
        label="product",
        remote_url=str(remote),
        expected_slug="example-org/service-a",
        refspec="refs/heads/main",
        object_path="VERSION_STATE.yml",
    )
    provider.finalize(binding)

    assert binding.raw_bytes == original_bytes
    assert binding.fetched_oid == binding.tip_oid_before == binding.tip_oid_after
    assert binding.fetch.exit_code == 0
    assert binding.tip_probe_before is not None
    assert binding.tip_probe_after is not None
    assert binding.replacement_refs_empty


def test_real_binding_detects_remote_tip_change_after_exact_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    work, remote = _local_remote(tmp_path, _state_bytes())
    monkeypatch.setattr(
        completion_module,
        "canonical_slug",
        lambda _url: "example-org/service-a",
    )
    bindings = tmp_path / "bindings"
    bindings.mkdir()
    provider = GitBindingProvider(bindings, runner=GitRunner(clock=lambda: FIXED_TIME))
    binding = provider.open(
        label="product",
        remote_url=str(remote),
        expected_slug="example-org/service-a",
        refspec="refs/heads/main",
        object_path="VERSION_STATE.yml",
    )

    (work / "VERSION_STATE.yml").write_bytes(_state_bytes(deployed="rel-8"))
    _git(work, "commit", "-q", "-am", "advance")
    _git(work, "push", "-q", "origin", "main")
    provider.finalize(binding)

    assert binding.tip_oid_after != binding.tip_oid_before
    assert binding.stable is False


def test_authenticated_http_remote_requires_the_explicit_credential_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _work, _remote = _local_remote(tmp_path, _state_bytes())

    class Handler(AuthenticatedGitHTTPHandler):
        project_root = tmp_path

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("local sandbox does not permit loopback listeners")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    remote_url = f"http://127.0.0.1:{server.server_port}/remote.git"
    monkeypatch.setattr(
        completion_module,
        "canonical_slug",
        lambda _url: "example-org/service-a",
    )

    try:
        unauthenticated_root = tmp_path / "unauthenticated-bindings"
        unauthenticated_root.mkdir()
        with pytest.raises(BindingFailure, match="product_ls_remote_before_failed_exit=128"):
            GitBindingProvider(
                unauthenticated_root,
                runner=GitRunner(clock=lambda: FIXED_TIME),
            ).open(
                label="product",
                remote_url=remote_url,
                expected_slug="example-org/service-a",
                refspec="refs/heads/main",
                object_path="VERSION_STATE.yml",
            )

        credential_file = tmp_path / "credentials"
        credential_file.write_text(
            f"http://user:pass@127.0.0.1:{server.server_port}\n",
            encoding="utf-8",
        )
        credential_file.chmod(0o600)
        helper = f"store --file={credential_file}"
        authenticated_root = tmp_path / "authenticated-bindings"
        authenticated_root.mkdir()
        provider = GitBindingProvider(
            authenticated_root,
            runner=GitRunner(clock=lambda: FIXED_TIME, credential_helper=helper),
        )
        binding = provider.open(
            label="product",
            remote_url=remote_url,
            expected_slug="example-org/service-a",
            refspec="refs/heads/main",
            object_path="VERSION_STATE.yml",
        )
        provider.finalize(binding)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    expected_config = ["-c", f"credential.helper={helper}"]
    assert binding.raw_bytes == _state_bytes()
    assert binding.injected_git_config_args == expected_config
    assert binding.tip_probe_before is not None
    assert binding.tip_probe_before.argv[1:3] == expected_config
    assert binding.fetch.argv[1:3] == expected_config
    assert binding.receipt_fields()["transport_url"] == remote_url
    assert "user:pass" not in json.dumps(binding.receipt_fields())
