"""Hallucination guard — surfaces commits whose body references a non-existent file.

A common failure mode of AI-generated commits: the body says "see
``src/auth/legacy.py``" when no such file exists at the merged SHA. This hook
detects backtick-quoted file-like references in the commit body and checks
that each exists at the commit's tree.

Built-in BranchHook. See ``docs/concepts/general-preferences.md`` § "1. No
hallucinated references in commits".

The hook is **best-effort**: only references that look like file paths
(contain a slash or dot, plausible extension) are checked. References to
symbols, classes, or English words in backticks are ignored.
"""

from __future__ import annotations

import re

from multiagent_protocol.skills.base import (
    BranchHookResult,
    CommitContext,
)

# Match backtick-quoted strings that look like file paths.
# Heuristic: must contain '/' or '.', extension length 1-5 alphanumeric,
# overall length 4-200 characters.
FILE_REF_RE = re.compile(r"`([a-zA-Z0-9_./-]{4,200}\.[a-zA-Z]{1,5})`")


class HallucinationGuardHook:
    name = "hook_hallucination_guard"

    def __init__(self, repo_path_resolver: callable | None = None) -> None:
        # Caller injects a function that returns True if a path exists in
        # the repo at the commit SHA. We do not couple the hook to the
        # GitHub API client; tests inject a simple in-memory resolver.
        self._resolver = repo_path_resolver

    def on_commit(self, commit: CommitContext) -> BranchHookResult:
        if self._resolver is None:
            # No resolver → cannot check. Best-effort.
            return BranchHookResult.none()

        body = commit.body or ""
        missing: list[str] = []
        seen: set[str] = set()
        for m in FILE_REF_RE.finditer(body):
            path = m.group(1)
            if path in seen:
                continue
            seen.add(path)
            try:
                exists = self._resolver(path, commit.sha)
            except Exception:
                # If the resolver fails (network blip, rate limit), skip.
                continue
            if not exists:
                missing.append(path)

        if not missing:
            return BranchHookResult.none()

        return BranchHookResult(
            incident_label="decision:hallucination-detected",
            incident_body=(
                f"Commit {commit.short_sha} references {len(missing)} "
                f"path(s) in its body that do not exist at the merged SHA. "
                f"Likely AI-hallucinated references.\n\n"
                f"Missing paths:\n"
                + "\n".join(f"- `{p}`" for p in missing)
                + "\n\nIf these references are intentional (e.g. paths to "
                "be added in a follow-up commit), close this issue with "
                "a comment explaining."
            ),
        )
