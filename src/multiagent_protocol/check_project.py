"""CLI adapter for the version-truth ``check-project --completion`` mode."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import NoReturn

from multiagent_protocol.version_truth.completion import (
    CompletionRequest,
    registry_url_for_slug,
    run_completion,
    seal_receipt,
    usage_failure_receipt,
)


class UsageError(ValueError):
    """An argument error that must be returned as a JSON receipt."""


class ReceiptArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = ReceiptArgumentParser(
        prog="multiagent-protocol check-project",
        description="Emit one exact-object declared-state completion subreceipt.",
    )
    parser.add_argument("project_id", nargs="?", help="Registry project identifier.")
    parser.add_argument(
        "--completion",
        action="store_true",
        help="Required: run the fail-closed completion profile.",
    )
    parser.add_argument(
        "--deployment-task-id",
        help="Opaque deployment task identifier to bind into the receipt.",
    )
    parser.add_argument(
        "--registry-origin-slug",
        help="Caller-trusted canonical GitHub owner/repository slug.",
    )
    parser.add_argument(
        "--registry-origin-url",
        help="Canonical GitHub URL. Defaults to HTTPS for --registry-origin-slug.",
    )
    parser.add_argument(
        "--product-origin-url",
        help="Canonical product transport URL; its slug must match the registry row.",
    )
    parser.add_argument(
        "--git-credential-helper",
        help="Auditable Git credential-helper command injected with per-call git -c.",
    )

    # Legacy check_project options are recognized so completion mode can reject
    # them with one structured receipt instead of argparse text on stderr.
    parser.add_argument("--registry", help=argparse.SUPPRESS)
    parser.add_argument("--registry-repo", help=argparse.SUPPRESS)
    parser.add_argument("--path", help=argparse.SUPPRESS)
    parser.add_argument("--base-dir", help=argparse.SUPPRESS)
    parser.add_argument("--ref", help=argparse.SUPPRESS)
    parser.add_argument(
        "--allow-working-tree",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--skip-fetch", action="store_true", help=argparse.SUPPRESS)
    return parser


def _emit(payload: dict) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    exact_argv: Sequence[str] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    observed_argv = list(sys.argv if exact_argv is None else exact_argv)
    try:
        args = build_parser().parse_args(arguments)
    except UsageError as exc:
        receipt, exit_code = usage_failure_receipt(
            exact_argv=observed_argv,
            reason=f"argument_error={exc}",
        )
        _emit(receipt)
        return exit_code

    forbidden: list[str] = []
    if not args.completion:
        forbidden.append("completion_mode_required")
    if args.registry is not None:
        forbidden.append("completion_rejects_registry_path_override")
    if args.registry_repo is not None:
        forbidden.append("completion_rejects_working_tree_registry_repo")
    if args.path is not None:
        forbidden.append("completion_rejects_product_working_tree_path")
    if args.base_dir is not None:
        forbidden.append("completion_rejects_product_base_dir")
    if args.allow_working_tree:
        forbidden.append("completion_rejects_allow_working_tree")
    if args.skip_fetch:
        forbidden.append("completion_rejects_skip_fetch")
    if args.ref is not None and args.ref != "origin/main":
        forbidden.append("completion_requires_ref=origin/main")
    if not args.project_id:
        forbidden.append("project_id_required")
    if not args.deployment_task_id:
        forbidden.append("deployment_task_id_required")
    if not args.registry_origin_slug:
        forbidden.append("registry_origin_slug_required")

    if forbidden:
        receipt, exit_code = usage_failure_receipt(
            exact_argv=observed_argv,
            reason=";".join(forbidden),
            project_id=args.project_id or "",
            deployment_task_id=args.deployment_task_id or "",
        )
        _emit(receipt)
        return exit_code

    registry_origin_url = args.registry_origin_url or registry_url_for_slug(
        args.registry_origin_slug
    )
    request = CompletionRequest(
        project_id=args.project_id,
        deployment_task_id=args.deployment_task_id,
        registry_origin_slug=args.registry_origin_slug,
        registry_origin_url=registry_origin_url,
        exact_argv=observed_argv,
        product_origin_url=args.product_origin_url,
        git_credential_helper=args.git_credential_helper,
    )
    try:
        receipt, exit_code = run_completion(request)
    except Exception as exc:  # noqa: BLE001 - CLI must emit one fail-closed receipt
        receipt, exit_code = usage_failure_receipt(
            exact_argv=observed_argv,
            reason=f"completion_internal_error={type(exc).__name__}",
            project_id=args.project_id,
            deployment_task_id=args.deployment_task_id,
        )
        exit_code = 1
        receipt["process"]["exit_code"] = exit_code
        # Re-seal after changing the fail-closed exit category.
        seal_receipt(receipt)
    _emit(receipt)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
