"""L5 break-glass auditor.

Detects ``[break-glass-*]``-prefixed commits on ``main`` and verifies:

1. The commit author is in ``config.owner.allowlisted_actors``.
2. An ADR exists under ``docs/decisions/`` within 24h of the commit's
   timestamp, referencing the commit's SHA.

Failures open ``decision:break-glass-unauthorized`` or
``decision:break-glass-unaudited`` incident issues.

Built-in BranchHook. See ``docs/concepts/break-glass.md`` and
``docs/concepts/general-preferences.md`` § "8. Break-glass requires ADR
within 24 hours".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from multiagent_protocol.skills.base import (
    BranchHook,
    BranchHookResult,
    CommitContext,
)

BREAK_GLASS_PREFIX_RE = re.compile(r"^\[break-glass-[a-z0-9-]+\]\s")


class BreakGlassAuditHook:
    name = "hook_break_glass_audit"

    def __init__(
        self,
        allowlisted_actors: tuple[str, ...] | None = None,
        adr_finder: callable | None = None,
        adr_deadline_hours: int = 24,
        clock: callable | None = None,
    ) -> None:
        # ``adr_finder(commit_sha) -> bool`` returns True iff a valid ADR
        # exists referencing this SHA in its body, with file mtime within
        # the deadline. Tests inject simple resolvers.
        # ``clock()`` returns the current time as UTC datetime; tests inject.
        self.allowlisted_actors = allowlisted_actors or ()
        self._adr_finder = adr_finder
        self.adr_deadline = timedelta(hours=adr_deadline_hours)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def on_commit(self, commit: CommitContext) -> BranchHookResult:
        if not BREAK_GLASS_PREFIX_RE.match(commit.subject):
            return BranchHookResult.none()

        # Check 1: actor allowlist.
        actor = commit.author_login or commit.committer_login
        if self.allowlisted_actors and actor not in self.allowlisted_actors:
            return BranchHookResult(
                incident_label="decision:break-glass-unauthorized",
                incident_body=(
                    f"Commit {commit.short_sha} has break-glass subject "
                    f"({commit.subject[:80]!r}) but author '{actor}' is not "
                    f"in the allowlisted actors set. This is either an "
                    f"unauthorized push or a misconfigured allowlist.\n\n"
                    f"Allowlisted: {', '.join(self.allowlisted_actors) or '(empty)'}"
                ),
            )

        # Check 2: ADR within deadline.
        if self._adr_finder is not None:
            adr_exists = self._adr_finder(commit.sha)
            if not adr_exists:
                # We do NOT yet know the commit's timestamp here (CommitContext
                # does not expose it; would require adding a field). For now,
                # the hook reports unaudited; the deadline is enforced by the
                # caller who checks adr-finder against the deadline window.
                return BranchHookResult(
                    incident_label="decision:break-glass-unaudited",
                    incident_body=(
                        f"Commit {commit.short_sha} uses break-glass subject "
                        f"({commit.subject[:80]!r}) but no ADR referencing "
                        f"this SHA was found under docs/decisions/. ADR is "
                        f"required within {int(self.adr_deadline.total_seconds() // 3600)} "
                        f"hours of the break-glass commit.\n\n"
                        f"To resolve: add a file `docs/decisions/NNNN_<topic>.md` "
                        f"with frontmatter `break_glass.commit_sha: \"{commit.sha}\"` "
                        f"and merge it. The hook re-checks on the next cron tick."
                    ),
                )

        return BranchHookResult.none()
