"""FEATURE A — L2 automatic revert-PR creation (opt-in, default OFF).

When ``env.yml`` ``auto_revert_pr: true`` and L2 post-merge re-validation finds
a **real** failure on ``main`` (the path that today only opens an incident
issue carrying the ``git revert <sha>`` command), the bot ALSO authors a revert
PR in the supervised repo and links it in the incident issue.

GitHub has **no revert REST endpoint**, and the engine is otherwise API-only
(``requests``). So the revert itself is done with git over a shallow clone:

1. ``git clone --depth <N> https://x-access-token:<token>@github.com/<repo>``
   into a tempdir (the App installation token authorises the push).
2. ``git revert --no-edit <bad-sha>`` on ``main``.
3. Amend the revert commit message to carry the five ``Agent-*`` trailers +
   ``Task-Ref`` (so the merge gate's own C5/L4 can evaluate the revert PR).
4. Push ``HEAD`` to ``revert/<bad-sha7>``.
5. Open the PR via the existing API client (:meth:`GitHubAPI.create_pull_request`).

**The revert PR goes through the normal gate** — it is deliberately NOT
auto-labelled ``ready-to-merge``. That is the whole point: a bot-authored
commit into a supervised repo is a Quadrant-D action, so it is owner/classifier
gated exactly like any other PR.

**Graceful degradation.** EVERY failure (clone, revert conflict, push, PR open)
degrades to today's behaviour: the incident issue is still opened; the failure
reason is appended to its body; the tick is never crashed. The bot commits
nothing on failure.

**Idempotency.** If the ``revert/<bad-sha7>`` branch — or an open PR from it —
already exists, the existing one is linked and no duplicate is created.

**Testability.** All git work goes through an injected ``runner`` callable
(default :func:`_subprocess_runner`), so unit tests exercise the whole flow
with the subprocess mocked and no real git. The GitHub side uses the existing
fakes.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Shallow-clone depth. Deep enough that ``git revert`` can almost always find
# the bad commit + its parent in history, shallow enough to stay cheap on a
# 5-minute tick. Matches the approved approach in the ADR.
CLONE_DEPTH = 50

# The revert PR's identity trailers. The revert is authored by the bot's CI
# identity, so Agent-Tool is github-actions and Agent-Model is n/a (the
# github-actions/manual carve-out C5 already recognises). Agent-Session uses a
# stable, well-formed ``s_`` value so C5's format check passes; Task-Ref is
# filled in per-incident with the incident issue ref.
REVERT_AGENT_TOOL = "github-actions"
REVERT_AGENT_MODEL = "n/a"
REVERT_AGENT_SESSION = "s_bot-revert"
REVERT_AGENT_MACHINE = "bot"


@dataclass(frozen=True)
class RunResult:
    """The outcome of one injected git invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# A runner takes an argv list + optional cwd and returns a RunResult. Injected
# so tests can mock every git call; the default shells out via subprocess.
Runner = Callable[..., RunResult]


@dataclass(frozen=True)
class RevertResult:
    """What the auto-revert attempt produced (never raises out of the module).

    - ``pr_url``: the revert PR URL when one was created OR an existing one was
      linked; ``None`` on any failure.
    - ``created``: True only when THIS call opened a new PR (False when it
      linked an existing branch/PR, or failed).
    - ``note``: a markdown fragment to append to the incident issue body —
      either the PR link or the failure reason. Always non-empty.
    """

    pr_url: str | None
    created: bool
    note: str


def _subprocess_runner(argv: list[str], *, cwd: str | None = None) -> RunResult:
    """Default runner: run ``argv`` and capture output (no shell).

    ``check=False`` — callers inspect ``returncode``; a non-zero git exit is a
    normal, handled outcome (e.g. a revert conflict), not an exception."""
    proc = subprocess.run(  # noqa: S603 - argv list, no shell, trusted args
        argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=180,
    )
    return RunResult(
        returncode=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or ""
    )


def revert_branch_name(bad_sha: str) -> str:
    """The deterministic revert branch for a bad commit: ``revert/<sha7>``."""
    return f"revert/{bad_sha[:7]}"


def _authed_clone_url(token: str, owner: str, repo: str) -> str:
    return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"


def _redact(text: str, token: str | None) -> str:
    """Strip the installation token out of any captured git output before it is
    logged or written into a public incident body."""
    if token:
        text = text.replace(token, "x-access-token:***")
    return text


def _trailer_block(task_ref: str) -> str:
    """The five identity trailers for the amended revert commit message."""
    return (
        f"Agent-Tool: {REVERT_AGENT_TOOL}\n"
        f"Agent-Model: {REVERT_AGENT_MODEL}\n"
        f"Agent-Session: {REVERT_AGENT_SESSION}\n"
        f"Agent-Machine: {REVERT_AGENT_MACHINE}\n"
        f"Task-Ref: {task_ref}"
    )


def _existing_revert_pr(api, owner: str, repo: str, branch: str) -> dict | None:
    """Return an open PR already opened from ``branch``, or None.

    Fail-safe: a lookup error returns None (treat as "no existing PR") rather
    than aborting the attempt — the branch-existence check below is the primary
    idempotency guard, and a duplicate PR is caught by GitHub's own 422 on
    create in the worst case."""
    try:
        prs = api.list_prs_for_head(owner, repo, branch, state="open")
    except Exception as e:  # noqa: BLE001 - fail-safe, logged
        logger.warning(
            "auto-revert: could not list PRs for head %s on %s/%s: %s",
            branch, owner, repo, e,
        )
        return None
    return prs[0] if prs else None


def ensure_revert_pr(
    api,
    owner: str,
    repo: str,
    bad_sha: str,
    *,
    token: str | None,
    incident_ref: str,
    base_branch: str = "main",
    runner: Runner = _subprocess_runner,
    clone_depth: int = CLONE_DEPTH,
) -> RevertResult:
    """Create (or link an existing) revert PR for ``bad_sha``. Never raises.

    ``incident_ref`` is the incident issue ref (e.g. ``Issue#42``) carried in
    the revert commit's ``Task-Ref`` trailer AND used in the PR body.
    """
    branch = revert_branch_name(bad_sha)

    # -- Idempotency: an existing branch / open PR is linked, not duplicated. --
    try:
        existing_sha = api.get_ref_sha(owner, repo, branch)
    except Exception as e:  # noqa: BLE001 - fail-safe
        logger.warning(
            "auto-revert: ref lookup for %s on %s/%s failed (%s); proceeding to "
            "attempt creation", branch, owner, repo, e,
        )
        existing_sha = None
    if existing_sha is not None:
        pr = _existing_revert_pr(api, owner, repo, branch)
        if pr is not None:
            url = pr.get("html_url") or f"{owner}/{repo}#{pr.get('number')}"
            logger.info(
                "auto-revert: revert PR already open for %s/%s@%s → %s (idempotent)",
                owner, repo, bad_sha[:7], url,
            )
            return RevertResult(
                pr_url=url, created=False,
                note=f"Auto-revert PR already open: {url}",
            )
        # Branch exists but no open PR (e.g. the previous PR was closed, or the
        # tick died between push and PR-open). Open the PR against the existing
        # branch head rather than re-doing the clone/revert.
        return _open_pr_for_branch(
            api, owner, repo, bad_sha, branch, base_branch, incident_ref,
            reused_branch=True,
        )

    if not token:
        # No installation token available (e.g. a test double with no App auth).
        # Cannot clone/push → degrade to incident-only.
        return RevertResult(
            pr_url=None, created=False,
            note="Auto-revert skipped: no installation token available to "
                 "clone/push the revert branch. Incident-only (opt-in feature "
                 "degraded gracefully).",
        )

    # -- Shallow clone → revert → amend trailers → push. --
    tmpdir = tempfile.mkdtemp(prefix="mgate-revert-")
    try:
        clone = runner(
            ["git", "clone", "--depth", str(clone_depth), "--branch", base_branch,
             _authed_clone_url(token, owner, repo), tmpdir],
        )
        if not clone.ok:
            return _fail(
                "clone", bad_sha, _redact(clone.stderr or clone.stdout, token)
            )

        rev = runner(["git", "revert", "--no-edit", bad_sha], cwd=tmpdir)
        if not rev.ok:
            # A revert CONFLICT is the common, expected failure. Abort the
            # in-progress revert so the working tree is clean, then degrade.
            runner(["git", "revert", "--abort"], cwd=tmpdir)
            return _fail(
                "revert-conflict", bad_sha,
                _redact(rev.stderr or rev.stdout, token),
                hint="`git revert` did not apply cleanly (likely a conflict "
                     "with a later change). Resolve and open the revert "
                     "manually.",
            )

        # Amend the revert commit to carry the identity trailers so the merge
        # gate (C5/L4) can evaluate the revert PR. Read the current message,
        # append the trailer block, re-commit with --amend.
        cur = runner(["git", "log", "-1", "--format=%B"], cwd=tmpdir)
        base_msg = (cur.stdout or "").strip() if cur.ok else f"Revert bad commit {bad_sha[:7]}"
        amended = f"{base_msg}\n\n{_trailer_block(incident_ref)}\n"
        amend = runner(
            ["git", "commit", "--amend", "-m", amended], cwd=tmpdir,
        )
        if not amend.ok:
            return _fail(
                "amend-trailers", bad_sha, _redact(amend.stderr or amend.stdout, token)
            )

        push = runner(
            ["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=tmpdir,
        )
        if not push.ok:
            return _fail(
                "push", bad_sha, _redact(push.stderr or push.stdout, token)
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # -- Open the PR via the API client (never auto-labelled ready-to-merge). --
    return _open_pr_for_branch(
        api, owner, repo, bad_sha, branch, base_branch, incident_ref,
        reused_branch=False,
    )


def _open_pr_for_branch(
    api,
    owner: str,
    repo: str,
    bad_sha: str,
    branch: str,
    base_branch: str,
    incident_ref: str,
    *,
    reused_branch: bool,
) -> RevertResult:
    """Open the revert PR from ``branch`` and return the linked result."""
    title = f"Revert bad commit {bad_sha[:7]} (post-merge re-validation)"
    body = (
        f"Automated revert of `{bad_sha}` opened by the L2 post-merge "
        f"re-validation auto-revert (`env.yml` `auto_revert_pr: true`).\n\n"
        f"This restores a known-good `{base_branch}` after the merged commit "
        f"failed post-merge re-validation ({incident_ref}). It goes through "
        f"the **normal merge gate** — it is not auto-approved; the classifier "
        f"rates it and the owner gate applies as usual. Label it "
        f"`decision:auto-revert` to fast-track it (Quadrant C)."
    )
    try:
        pr = api.create_pull_request(
            owner, repo, title=title, head=branch, base=base_branch, body=body,
        )
    except Exception as e:  # noqa: BLE001 - fail-safe (branch is pushed, PR failed)
        note = (
            f"Auto-revert branch `{branch}` was pushed, but opening the revert "
            f"PR failed: {e}. Open a PR from `{branch}` manually."
        )
        logger.warning("auto-revert: %s", note)
        return RevertResult(pr_url=None, created=False, note=note)
    url = pr.get("html_url") or f"{owner}/{repo}#{pr.get('number')}"
    verb = "opened on reused branch" if reused_branch else "opened"
    logger.info("auto-revert: revert PR %s for %s/%s@%s → %s",
                verb, owner, repo, bad_sha[:7], url)
    # This call opened a NEW PR (whether or not the branch was reused), so it
    # counts toward the auto_revert_prs metric. Only the "existing OPEN PR was
    # linked" path (handled in ensure_revert_pr) returns created=False.
    return RevertResult(
        pr_url=url, created=True,
        note=f"Auto-revert PR opened (goes through the normal gate): {url}",
    )


def _fail(stage: str, bad_sha: str, detail: str, *, hint: str = "") -> RevertResult:
    """Build a graceful-degradation RevertResult with the failure reason."""
    detail = (detail or "").strip()
    # Keep the incident body bounded — git can be very chatty.
    if len(detail) > 800:
        detail = detail[:800] + " …(truncated)"
    parts = [
        f"Auto-revert PR **not** created (stage: `{stage}`). Falling back to "
        f"incident-only; revert `{bad_sha[:7]}` manually."
    ]
    if hint:
        parts.append(hint)
    if detail:
        parts.append(f"```\n{detail}\n```")
    note = "\n\n".join(parts)
    logger.warning(
        "auto-revert: stage %s failed for %s: %s", stage, bad_sha[:7], detail[:200]
    )
    return RevertResult(pr_url=None, created=False, note=note)
