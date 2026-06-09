"""Unauthorized-push detector (the code-level alternative to branch protection).

GitHub branch protection ("only the App may push to ``main``") is a paid
feature on private repos. This BranchHook is the protocol's code-level
substitute: it watches ``main`` and flags any commit that did **not** arrive
through a sanctioned path.

A commit on ``main`` is sanctioned iff **any** of:

1. its committer is the bot itself (a normal squash-merge the bot performed), OR
2. its subject is a break-glass commit (``[break-glass-*]``) — those are the L5
   break-glass auditor's job (actor allowlist + ADR-within-deadline), so this
   hook stays out of its lane to avoid double-incidenting, OR
3. its committer login is in the owner allowlist (an allowlisted human pushing
   directly — already a trusted actor).

Anything else — a non-bot, non-break-glass commit by a login that is not on the
allowlist — is an **unauthorized push**: someone (or some compromised token)
wrote to ``main`` around the merge gate. The hook opens a
``decision:unauthorized-push`` incident (idempotently, via the supervisor's
existing open-issue dedupe on the commit SHA).

Built-in BranchHook. Mirrors :mod:`hook_break_glass_audit` in shape. Run by
``branch_supervisor.scan_repo`` over every supervised repo, including
audit-only repos (DEC-C) — so the governance repo's ``main`` is watched even
though its PRs are not gated.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    BranchHookResult,
    CommitContext,
)
from multiagent_protocol.skills.builtin.hook_break_glass_audit import (
    # Reuse the EXACT break-glass regex so the two hooks agree on what counts as
    # a break-glass commit: anything the L5 auditor claims, this hook must skip.
    BREAK_GLASS_PREFIX_RE,
)

INCIDENT_LABEL = "decision:unauthorized-push"


class UnauthorizedPushHook:
    name = "hook_unauthorized_push"

    def __init__(
        self,
        bot_user: str | None = None,
        allowlisted_actors: tuple[str, ...] | None = None,
    ) -> None:
        # Injected by the orchestrator. With the defaults (no bot user, empty
        # allowlist) the loader's 0-arg instance would flag every non-break-glass
        # commit — so, like the other identity-bound builtins, it is only armed
        # once configured. The runtime always injects the real bot_user +
        # allowlist; the 0-arg instance is never used for a real scan.
        self.bot_user = bot_user
        self.allowlisted_actors = tuple(allowlisted_actors or ())

    def on_commit(self, commit: CommitContext) -> BranchHookResult:
        # (1) Bot's own merge commit → sanctioned.
        if self.bot_user is not None and commit.committer_login == self.bot_user:
            return BranchHookResult.none()

        # (2) Break-glass commit → the L5 break-glass auditor owns it.
        if BREAK_GLASS_PREFIX_RE.match(commit.subject):
            return BranchHookResult.none()

        # (3) Allowlisted human pushing directly → trusted actor.
        if commit.committer_login is not None and (
            commit.committer_login in self.allowlisted_actors
        ):
            return BranchHookResult.none()

        # Otherwise: an unsanctioned write to main.
        return BranchHookResult(
            incident_label=INCIDENT_LABEL,
            incident_body=(
                f"Commit {commit.short_sha} landed on `main` but is not a "
                f"sanctioned write:\n\n"
                f"- committer login: `{commit.committer_login or '(unknown)'}`\n"
                f"- subject: {commit.subject[:80]!r}\n\n"
                f"It was not authored by the bot (`{self.bot_user or '(unset)'}`), "
                f"is not a `[break-glass-*]` commit, and the committer is not in "
                f"the owner allowlist "
                f"({', '.join(self.allowlisted_actors) or '(empty)'}).\n\n"
                f"This is the code-level branch-protection check: someone — or a "
                f"compromised token — wrote to `main` around the merge gate. "
                f"Verify the push was intended. If it was a legitimate emergency, "
                f"it should have used the break-glass flow "
                f"(`[break-glass-*]` subject + ADR within the deadline)."
            ),
        )
