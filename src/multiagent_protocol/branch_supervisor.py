"""Module 2 — branch_supervisor.

Runs L2 (post-merge re-validation) + L5 (break-glass auditor) per supervised
repo per cron tick. Operates on ``main`` HEAD, not on open PRs.

Watermarks: ``bot-state/branch_supervisor_watermarks.json`` tracks the last
commit each supervised repo has been processed up to. It is persisted durably
on a dedicated ``bot-state`` branch of the governance repo (see
:class:`BotStateStore`) — never on ``main``, so the bot's own main-scans can
never observe its state commits — with an atomic local-file cache per run.
Re-processing the same commit on every tick would be O(N) on commit count;
the watermark makes it O(delta).

Both run here: L5 break-glass detection and L2 post-merge re-validation
(``revalidate_main`` below — detection + incident; the caller may additionally
open an opt-in automatic revert PR for a proven non-merge target).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from multiagent_protocol.github_api import (
    SINCE_NOT_FOUND,
    GitHubAPI,
    SecondaryRateLimitError,
)
from multiagent_protocol.skills.base import BranchHook
from multiagent_protocol.skills.builtin.validator_ci_green import (
    DEFAULT_CHECK_PUBLISHER,
    publisher_satisfied,
)
from multiagent_protocol.trailers import parse_trailers
from multiagent_protocol.types import CommitContext

logger = logging.getLogger(__name__)

WATERMARKS_PATH = Path("bot-state/branch_supervisor_watermarks.json")

# The dedicated branch + path on the governance repo that hold the durable
# watermark state. Persisting to a NON-main branch is deliberate: the engine's
# own L2/L5/unauthorized-push scanners only look at ``main``, so they never see
# these state commits and cannot self-trigger an incident on them.
BOT_STATE_BRANCH = "bot-state"
BOT_STATE_PATH = "bot-state/branch_supervisor_watermarks.json"

# Hard cap on commits PROCESSED per repo per tick (per L-layer). A tick has a
# 5-min budget; bounding the per-tick work keeps progress monotonic and the
# tick within budget even when a large backlog accrues. The watermark advances
# past exactly the commits processed, so the next tick continues where this one
# stopped.
MAX_COMMITS_PER_TICK = 100

# Reserved key under the watermark dict holding L2 first-seen timestamps for
# still-unsettled commits: ``{"<owner>/<repo>:<sha>": "<iso8601>"}``. Kept in
# the same persisted JSON so the stall deadline survives across (stateless)
# ticks. Reserved keys start with ``_`` so they never collide with a real
# ``<owner>/<repo>`` watermark key.
L2_UNSETTLED_KEY = "_l2_unsettled"

# How long an L2 commit may stay unsettled (only infra/cancelled-style failures,
# or a still-running required check) before the supervisor stops silently
# retrying it forever and escalates: opens ONE ``decision:l2-stalled`` incident
# and force-settles the commit as a real failure.
L2_STALL_DEADLINE_HOURS = 24


@dataclass(frozen=True)
class SupervisorIncident:
    """One incident the supervisor wants to surface as a GitHub issue."""

    commit_sha: str
    label: str
    body: str


def load_watermarks(path: Path = WATERMARKS_PATH) -> dict[str, Any]:
    """Load the local watermark cache.

    FAIL CLOSED on a corrupt/unreadable persisted file: a parse failure is
    raised, not swallowed into an empty dict. Returning ``{}`` would silently
    re-bootstrap (or, pre-bootstrap, re-flood) — exactly the failure mode this
    reliability work exists to prevent. A genuinely absent file is the only
    "empty is fine" case (first run / fresh checkout)."""
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"watermark cache unreadable at {path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"watermark cache corrupt at {path} (fail-closed; refusing to "
            f"re-bootstrap from empty): {e}"
        ) from e
    if not isinstance(data, dict):
        raise RuntimeError(
            f"watermark cache at {path} is not a JSON object: {type(data).__name__}"
        )
    return data


def save_watermarks(
    watermarks: dict[str, Any], path: Path = WATERMARKS_PATH
) -> None:
    """Persist the watermark cache atomically (tempfile + ``os.replace``).

    Writing in place risks a half-written file if the 5-min tick is killed
    mid-write; the next tick would then fail-closed on the truncated JSON. A
    same-directory tempfile + atomic rename guarantees readers see either the
    old file or the fully-written new one, never a partial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(watermarks, indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".wm-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def bootstrap_watermark_if_absent(
    api: GitHubAPI,
    owner: str,
    repo: str,
    watermarks: dict[str, Any],
    *,
    key_suffix: str = "",
) -> str | None:
    """Bootstrap a missing watermark to the repo's current ``main`` HEAD.

    Gate doctrine cannot be applied retroactively to pre-activation history, and
    a cold start with no watermark would otherwise re-walk (and re-flood on)
    the entire history. So the FIRST time a repo+layer is seen, we set its
    watermark to ``main`` HEAD (one ``main_head_sha`` call) and SCAN NOTHING for
    it this tick — the caller persists immediately and skips the scan.

    Returns the HEAD SHA when a bootstrap happened (caller must persist + skip),
    or ``None`` when a watermark already existed (caller proceeds to scan).

    A watermark is "present" only when it is a non-empty SHA string. A key that
    is present but ``None`` / ``""`` / non-string is treated as ABSENT and
    re-bootstrapped: such an entry is corrupt (a stale local cache from a prior
    run whose durable push failed, or a hand-edited null) and ``in watermarks``
    alone would mistake it for a real watermark — then the caller would scan
    with ``since=None`` and full-walk the entire history (the production
    cold-start flood this function exists to prevent)."""
    repo_key = f"{owner}/{repo}{key_suffix}"
    existing = watermarks.get(repo_key)
    if isinstance(existing, str) and existing:
        return None
    head = api.main_head_sha(owner, repo)
    watermarks[repo_key] = head
    logger.info(
        "bootstrapped watermark %s -> %s (HEAD); not scanning pre-activation "
        "history this tick", repo_key, head[:7],
    )
    return head


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def count_l2_unsettled(watermarks: dict[str, Any]) -> int:
    """Number of commits currently tracked as L2-unsettled (for tick metrics)."""
    tracked = watermarks.get(L2_UNSETTLED_KEY)
    return len(tracked) if isinstance(tracked, dict) else 0


def scan_repo(
    api: GitHubAPI,
    owner: str,
    repo: str,
    hooks: list[BranchHook],
    watermarks: dict[str, Any],
    *,
    max_commits: int = MAX_COMMITS_PER_TICK,
) -> tuple[list[SupervisorIncident], str | None]:
    """Scan ``main`` for new commits since the watermark, run every hook.

    Returns ``(incidents, new_watermark)``. The caller is responsible for
    persisting the new watermark and opening/labelling issues for each
    incident. Processing is BOUNDED to ``max_commits`` per tick (oldest-first):
    the watermark advances to exactly the newest commit processed, so a large
    backlog drains monotonically over several ticks without blowing the budget.

    If the watermark cannot be located within the bounded history walk (``main``
    rewritten off the watermark), the watermark is re-bootstrapped to HEAD and a
    single ``decision:watermark-lost`` incident is surfaced — no full replay.
    """
    repo_key = f"{owner}/{repo}"
    since = watermarks.get(repo_key)

    # SAFETY NET (defense in depth): a missing / invalid watermark must NEVER
    # trigger a full-history walk. ``list_commits_on_main(since_sha=None)``
    # returns up to ``LIST_COMMITS_MAX_PAGES`` × 100 commits — on a repo with
    # deep history that is precisely the multi-minute walk + incident flood +
    # tick timeout observed in production. The caller bootstraps to HEAD before
    # scanning, but if a stale local cache or a null persisted entry slips a
    # ``None`` watermark through, we bootstrap-to-HEAD here too and scan nothing
    # this tick rather than replay history. (``bootstrap_watermark_if_absent``
    # is the first line of defence; this is the second.)
    if not (isinstance(since, str) and since):
        head = api.main_head_sha(owner, repo)
        watermarks[repo_key] = head
        logger.warning(
            "scan_repo %s: no valid watermark (since=%r) — bootstrapping to HEAD "
            "%s and scanning nothing this tick (refusing full-history walk)",
            repo_key, since, head[:7],
        )
        return [], head

    raw_commits = api.list_commits_on_main(owner, repo, since_sha=since)
    if raw_commits is SINCE_NOT_FOUND:
        return _recover_watermark_lost(api, owner, repo, watermarks, repo_key, since)
    if not raw_commits:
        return [], since

    # Oldest-first, then cap to this tick's budget. The watermark advances to
    # the newest commit we actually processed (NOT the absolute HEAD), so any
    # unprocessed remainder is picked up next tick.
    oldest_first = list(reversed(raw_commits))[:max_commits]
    if len(raw_commits) > max_commits:
        logger.info(
            "L5 scan %s: %d new commits exceed per-tick cap %d; processing "
            "oldest %d, remainder next tick",
            repo_key, len(raw_commits), max_commits, max_commits,
        )
    commits = [_to_commit_context(c) for c in oldest_first]

    incidents: list[SupervisorIncident] = []
    for commit in commits:
        for hook in hooks:
            try:
                result = hook.on_commit(commit)
            except Exception as e:
                logger.error(
                    "hook '%s' raised on commit %s: %s",
                    hook.name, commit.short_sha, e,
                )
                continue
            if result.incident_label is not None:
                incidents.append(SupervisorIncident(
                    commit_sha=commit.sha,
                    label=result.incident_label,
                    body=result.incident_body or "",
                ))

    new_watermark = commits[-1].sha  # newest commit actually processed
    return incidents, new_watermark


def _recover_watermark_lost(
    api: GitHubAPI,
    owner: str,
    repo: str,
    watermarks: dict[str, Any],
    repo_key: str,
    lost_sha: str | None,
) -> tuple[list[SupervisorIncident], str | None]:
    """Re-bootstrap to HEAD after the watermark fell off ``main`` history.

    Opens ONE ``decision:watermark-lost`` incident (idempotent per SHA) and sets
    the watermark to current HEAD so the next tick resumes from there — instead
    of replaying the entire history (which re-floods and blows the budget)."""
    head = api.main_head_sha(owner, repo)
    watermarks[repo_key] = head
    logger.warning(
        "watermark %s (%s) not found within bounded history walk on %s — main "
        "history was likely rewritten; re-bootstrapping to HEAD %s",
        repo_key, (lost_sha or "")[:7], f"{owner}/{repo}", head[:7],
    )
    incident = SupervisorIncident(
        commit_sha=head,
        label="decision:watermark-lost",
        body=(
            f"The `branch_supervisor` watermark for `{owner}/{repo}` "
            f"(`{repo_key}` = `{(lost_sha or '?')[:12]}`) could not be located "
            f"on `main` within the bounded history walk.\n\n"
            f"This almost always means `main` history was rewritten "
            f"(force-push / branch reset) out from under the gate. Rather than "
            f"replay the entire history (which would re-flood incidents and "
            f"exceed the tick budget), the watermark has been re-bootstrapped "
            f"to the current HEAD `{head[:12]}`. Commits between the lost "
            f"watermark and HEAD were NOT re-scanned.\n\n"
            f"If the rewrite was unexpected, audit `main`'s reflog / force-push "
            f"history for this repo."
        ),
    )
    return [incident], head


def _to_commit_context(raw: dict) -> CommitContext:
    msg = raw["commit"]["message"]
    subject, _, body = msg.partition("\n")
    # GitHub returns the commit author/committer time under
    # commit.committer.date (ISO-8601 UTC). The committer date is the
    # one we want for break-glass deadline checks — author date can be
    # rewritten by `git commit --amend --date=...` whereas committer
    # date reflects when the push actually landed.
    committed_at = ((raw.get("commit") or {}).get("committer") or {}).get("date")
    return CommitContext(
        sha=raw["sha"],
        subject=subject,
        body=body.lstrip("\n"),
        author_login=(raw.get("author") or {}).get("login"),
        committer_login=(raw.get("committer") or {}).get("login"),
        parents=tuple(p["sha"] for p in raw.get("parents", [])),
        trailers=parse_trailers(msg),
        committed_at=committed_at,
    )


# ---------------------------------------------------------------------------
# L2 — post-merge re-validation
#
# For each commit on ``main`` newer than the L2 watermark, re-run the required
# checks against the merged SHA (``docs/concepts/architecture.md`` § "L2").
# A *real* failure opens a ``decision:post-merge-revalidation`` incident; an
# *infra* failure (cancelled / zero-duration) is left unsettled so the next
# tick retries it; ``skipped`` is an intentional protocol skip and passes.
#
# This module emits detection + incident. The caller can additionally request
# the default-off automatic revert-PR path; that path independently verifies
# the target's parent structure and refuses multi-parent commits fail-closed.
# Manual incident guidance stays safe for both merge and non-merge targets.
# ---------------------------------------------------------------------------

# Conclusions that mean "the check did not really run" (not a code failure).
_INFRA_CONCLUSIONS = {"cancelled"}
# Conclusions that count as passing for L2 purposes (no explicit required list:
# every check is its own opt-in, and ``skipped`` is a legit intentional skip of
# an optional workflow's ``if:``).
_PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}
# Conclusions that count as passing for a *named REQUIRED* check. ``skipped`` is
# deliberately EXCLUDED: a required check that resolves ``skipped`` did not
# actually run, so passing L2 on it would let a regression slip exactly where
# the operator demanded a green signal. A skipped required check is treated as
# unsettled → it rides the stall deadline and escalates if it never resolves.
_PASSING_REQUIRED_CONCLUSIONS = {"success", "neutral"}


def _is_infra_failure(check: dict) -> bool:
    """True iff a non-passing check looks like infrastructure, not code.

    Per doctrine: ``cancelled`` (workflow killed mid-run, e.g. Actions-minutes
    exhaustion) or zero-duration (``started_at == completed_at`` → the runner
    queue rejected it, it never executed). ``skipped`` is NOT infra — it means
    the workflow's own ``if:`` evaluated false (an intentional protocol skip).
    """
    if check.get("conclusion") in _INFRA_CONCLUSIONS:
        return True
    started, completed = check.get("started_at"), check.get("completed_at")
    if started and completed and started == completed:
        return True
    return False


def _classify_commit_checks(
    api: GitHubAPI,
    owner: str,
    repo: str,
    sha: str,
    required_checks: tuple[str, ...],
    allow_no_ci: bool = False,
    expected_check_publisher: str | None = DEFAULT_CHECK_PUBLISHER,
) -> tuple[str, list[str]]:
    """Return ``(status, failing_names)`` for one merged commit.

    ``status`` is ``"passed"``, ``"real_failure"``, or ``"infra"``.

    ``expected_check_publisher`` mirrors C2's publisher trust on the post-merge
    side: a NAMED required check counts as satisfied only if at least one of its
    same-named passing runs was published by that App. A required check that is
    green only because a FOREIGN App published it is treated as not-satisfied →
    a real failure (the L2 incident path) — so a foreign-published required
    check can no longer slip through L2 the way it is blocked at C2. ``None``
    skips the publisher gate (publisher-agnostic / legacy).
    """
    checks = api.check_runs(owner, repo, sha)

    if required_checks:
        # R1 in L2: mirror C2's fail-closed semantics. A named required check
        # that is MISSING on the merged commit is a REAL failure (open the
        # incident), regardless of allow_no_ci — allow_no_ci only relaxes the
        # NO-required-list path below. Inspect ALL same-named runs so a
        # duplicate failing check is not masked by a same-named success.
        real: list[str] = []
        infra = False
        for name in required_checks:
            runs = [c for c in checks if c.get("name") == name]
            if not runs:
                real.append(name)  # specified-and-missing → real failure
                continue
            completed = [c for c in runs if c.get("status") == "completed"]
            if not completed:
                # Present but still running (queued/in_progress) → UNSETTLED.
                # Do NOT pass + advance the watermark, or a required check that
                # later fails would be missed (it landed before CI finished).
                # Mark infra so the tick retries this commit next time.
                infra = True
                continue
            name_real = False
            name_infra = False
            passing_slugs: list[str | None] = []
            for c in completed:
                if c.get("conclusion") in _PASSING_REQUIRED_CONCLUSIONS:
                    passing_slugs.append((c.get("app") or {}).get("slug"))
                    continue
                if c.get("conclusion") == "skipped":
                    # A REQUIRED check resolved ``skipped`` → it never ran;
                    # leave unsettled (retry → stall-escalate) rather than pass.
                    name_infra = True
                    continue
                if _is_infra_failure(c):
                    name_infra = True
                else:
                    name_real = True
                    real.append(c.get("name", "?"))
            # Publisher trust (mirrors C2's named-required path): only fire when
            # the check is otherwise green for this name — a foreign-published
            # green required check is then a REAL failure (not-satisfied), never
            # silently passed. Foreign non-success runs already failed above.
            if (
                not name_real and not name_infra
                and not publisher_satisfied(passing_slugs, expected_check_publisher)
            ):
                name_real = True
                real.append(name)
            if name_infra and not name_real:
                infra = True
        if real:
            return "real_failure", real
        if infra:
            return "infra", []
        return "passed", []

    # No explicit required list: every check on the commit is relevant.
    relevant = list(checks)
    if not relevant:
        # No checks on this commit. Mirror C2's fail-closed stance: settle it
        # only if the operator opted into allow_no_ci; otherwise leave it
        # unsettled (retry) so a regression that landed before CI started — or
        # in a repo that silently dropped CI — is not quietly passed.
        return ("passed" if allow_no_ci else "infra"), []
    real = []
    infra = False
    for c in relevant:
        if c.get("status") != "completed":
            # Present but still running (queued/in_progress) → UNSETTLED, exactly
            # as the named-required path treats it above. Do NOT pass + advance
            # the watermark while a check is still running, or a check that later
            # fails would be permanently missed (it landed before CI finished).
            # Mark infra so the tick retries this commit next time.
            infra = True
            continue
        if c.get("conclusion") in _PASSING_CONCLUSIONS:
            continue
        if _is_infra_failure(c):
            infra = True
        else:
            real.append(c.get("name", "?"))
    if real:
        return "real_failure", real
    if infra:
        return "infra", []
    return "passed", []


def revalidate_main(
    api: GitHubAPI,
    owner: str,
    repo: str,
    required_checks: tuple[str, ...],
    watermarks: dict[str, Any],
    *,
    l2_key_suffix: str = ":l2",
    allow_no_ci: bool = False,
    expected_check_publisher: str | None = DEFAULT_CHECK_PUBLISHER,
    max_commits: int = MAX_COMMITS_PER_TICK,
    stall_deadline_hours: int = L2_STALL_DEADLINE_HOURS,
    clock: Callable[[], datetime] | None = None,
) -> tuple[list[SupervisorIncident], str | None]:
    """L2: re-validate merged commits on ``main`` since the L2 watermark.

    Returns ``(incidents, new_watermark)``. The watermark advances only past
    *settled* commits (passed, or real-failure incident raised). A commit with
    only infra-failures is left unsettled (the watermark stops before it) so a
    later tick re-checks it once the runner recovers — BUT only up to a deadline:
    a commit that stays unsettled past ``stall_deadline_hours`` (e.g. a
    permanently-``cancelled`` check that would otherwise be retried forever in
    silence) escalates to exactly ONE ``decision:l2-stalled`` incident and is
    force-settled as a real failure, so one stuck commit can no longer silently
    halt L2 for the repo. Every unsettled break is logged (repo, sha, reason).

    Processing is bounded to ``max_commits`` per tick; the watermark advances to
    the newest settled commit within that budget. A watermark that fell off
    ``main`` history re-bootstraps to HEAD with a ``decision:watermark-lost``
    incident (no full replay).

    ``expected_check_publisher`` applies C2's publisher trust to the post-merge
    re-validation: a named required check is satisfied only by a same-named run
    from that App; a foreign-published green required check is treated as
    not-satisfied → the normal L2 incident path. It defaults to
    ``"github-actions"``. The caller (``main.py``) passes the effective per-repo
    / env publisher (``config.projects.effective_expected_check_publisher(full,
    config.env.expected_check_publisher) or DEFAULT_CHECK_PUBLISHER``), so
    per-repo / env overrides apply to L2 exactly as they do to C2.
    """
    now = (clock or _utcnow)()
    deadline = timedelta(hours=stall_deadline_hours)
    repo_key = f"{owner}/{repo}{l2_key_suffix}"
    since = watermarks.get(repo_key)

    # SAFETY NET: never full-walk on a missing / invalid watermark (identical
    # reasoning to ``scan_repo`` above — a ``None`` here would re-validate the
    # entire history and time the tick out). Bootstrap-to-HEAD and settle
    # nothing this tick.
    if not (isinstance(since, str) and since):
        head = api.main_head_sha(owner, repo)
        watermarks[repo_key] = head
        logger.warning(
            "revalidate_main %s: no valid watermark (since=%r) — bootstrapping to "
            "HEAD %s and re-validating nothing this tick", repo_key, since, head[:7],
        )
        return [], head

    raw_commits = api.list_commits_on_main(owner, repo, since_sha=since)
    if raw_commits is SINCE_NOT_FOUND:
        return _recover_watermark_lost(api, owner, repo, watermarks, repo_key, since)
    if not raw_commits:
        return [], since

    unsettled = watermarks.setdefault(L2_UNSETTLED_KEY, {})
    if not isinstance(unsettled, dict):  # corrupt/legacy → reset this sub-tree
        unsettled = {}
        watermarks[L2_UNSETTLED_KEY] = unsettled

    incidents: list[SupervisorIncident] = []
    new_watermark = since
    oldest_first = list(reversed(raw_commits))[:max_commits]
    if len(raw_commits) > max_commits:
        logger.info(
            "L2 %s: %d new commits exceed per-tick cap %d; processing oldest "
            "%d, remainder next tick", repo_key, len(raw_commits),
            max_commits, max_commits,
        )
    for raw in oldest_first:
        sha = raw["sha"]
        stall_id = f"{owner}/{repo}:{sha}"
        status, failing = _classify_commit_checks(
            api, owner, repo, sha, required_checks, allow_no_ci=allow_no_ci,
            expected_check_publisher=expected_check_publisher,
        )
        if status == "infra":
            first_seen = _parse_iso(unsettled.get(stall_id))
            if first_seen is None:
                first_seen = now
                unsettled[stall_id] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            if now - first_seen >= deadline:
                # Stuck past the deadline → escalate ONCE and force-settle as a
                # real failure (cancelled+stale is treated as a real failure per
                # policy). Removing it from `unsettled` lets the watermark
                # advance, so it stops blocking L2 for this repo.
                logger.warning(
                    "L2 STALL %s sha=%s: unsettled since %s (> %dh) — opening "
                    "decision:l2-stalled and force-settling",
                    f"{owner}/{repo}", sha[:7], unsettled[stall_id],
                    stall_deadline_hours,
                )
                incidents.append(SupervisorIncident(
                    commit_sha=sha,
                    label="decision:l2-stalled",
                    body=_l2_stalled_body(
                        owner, repo, sha, unsettled[stall_id], stall_deadline_hours
                    ),
                ))
                unsettled.pop(stall_id, None)
                new_watermark = sha  # force-settled
                continue
            # Within grace → stop here, retry next tick. Log the break so a
            # silent halt is now observable (repo, sha, reason).
            logger.info(
                "L2 unsettled break %s sha=%s reason=infra/cancelled first_seen=%s",
                f"{owner}/{repo}", sha[:7], unsettled[stall_id],
            )
            break
        # Settled (passed or real failure) → clear any prior unsettled record.
        unsettled.pop(stall_id, None)
        if status == "real_failure":
            incidents.append(SupervisorIncident(
                commit_sha=sha,
                label="decision:post-merge-revalidation",
                body=_l2_incident_body(owner, repo, sha, failing),
            ))
        new_watermark = sha  # settled (passed or incident-raised)

    return incidents, new_watermark


def _l2_stalled_body(
    owner: str, repo: str, sha: str, first_seen: str, deadline_hours: int
) -> str:
    return (
        f"**L2 post-merge re-validation STALLED** on `{owner}/{repo}` at "
        f"`{sha[:7]}`.\n\n"
        f"This commit's required check(s) have been stuck in an unsettled "
        f"(infrastructure / `cancelled` / never-completed) state since "
        f"`{first_seen}` — more than {deadline_hours} hours. L2 retries "
        f"unsettled commits every tick, but a commit that never settles would "
        f"otherwise halt post-merge re-validation for this repo **forever, "
        f"silently**.\n\n"
        f"Per policy a commit stuck past the deadline is treated as a real "
        f"failure: the watermark has been force-advanced past it so L2 resumes "
        f"for newer commits, and this incident records why.\n\n"
        f"Investigate the stuck check (re-run CI on `{sha[:7]}`; if the code is "
        f"actually bad, follow the parent-aware revert procedure below).\n\n"
        f"{_manual_revert_guidance(sha)}"
    )


def _l2_incident_body(owner: str, repo: str, sha: str, failing: list[str]) -> str:
    checks = ", ".join(f"`{n}`" for n in failing) or "(unknown)"
    return (
        f"**Post-merge re-validation failed** on `{owner}/{repo}` at "
        f"`{sha[:7]}`.\n\n"
        f"Failing required check(s): {checks}\n\n"
        f"These checks passed (or were absent) at merge time but fail on the "
        f"merged commit — a real regression on `main`, not an infrastructure "
        f"blip (cancelled / never-run checks are ignored).\n\n"
        f"To restore a known-good `main`, follow the parent-aware revert "
        f"procedure below.\n\n"
        f"{_manual_revert_guidance(sha)}\n\n"
        f"then open a PR — label it `decision:auto-revert` so the classifier "
        f"fast-tracks it (Quadrant C)."
    )


def _manual_revert_guidance(sha: str) -> str:
    """Parent-aware manual revert guidance safe for merge and non-merge SHAs."""
    return (
        "Inspect the target's parents before choosing a revert command:\n\n"
        f"`git show --format=%P {sha}`\n\n"
        f"- Zero or one parent: after validation, run `git revert {sha}`.\n"
        "- Two or more parents: identify and validate the mainline parent, then "
        f"run `git revert -m N {sha}` manually (replace `N` with the validated "
        "parent number)."
    )


# ---------------------------------------------------------------------------
# Durable watermark persistence (bot-state branch).
#
# The bot is stateless across cron ticks (checkout-clean wipes the tree every
# run), so the watermark must live in GitHub itself. We persist the JSON to a
# DEDICATED ``bot-state`` branch of the governance repo via the App's
# contents:write — never to ``main``, so the engine's own main-scanning
# L2/L5/unauthorized-push hooks never observe these state commits and cannot
# self-trigger. The local file is kept as a fast within-run cache; the
# bot-state branch is the source of truth restored at the start of each tick.
# ---------------------------------------------------------------------------


def _is_transient_push_error(e: BaseException) -> bool:
    """True iff a bot-state push failure is a survivable transient / race.

    Only two cases are survivable (skip this push, retry next tick):

    * a stale-precondition conflict (**409** or **422**) — a concurrent tick
      advanced the file between our blob-SHA re-read and our PUT, so our ``sha``
      precondition is out of date (its newer state already persisted, so losing
      this push is harmless), and
    * a secondary-rate-limit — inherently transient, already retried at the
      repo level next tick.

    Everything else — a **403** (missing ``contents:write``), any other 4xx,
    an exhausted-retry 5xx, or a non-HTTP error — is a hard failure that would
    never succeed on retry, so the caller fails the tick closed rather than
    cold-start L2/L5 forever.

    A 409/422 is treated as the stale-precondition case ONLY when its body
    references the SHA mismatch. A 409/422 for any OTHER reason (validation /
    bad request) would recur on every retry, so swallowing it would silently
    skip persistence forever — that is classed HARD (fail closed). If the body
    cannot be read, fail closed (do not assume survivable).
    """
    if isinstance(e, SecondaryRateLimitError):
        return True
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    if status not in (409, 422):
        return False
    try:
        body = resp.json()
    except Exception:
        return False  # unreadable body → fail closed, don't assume survivable
    return "sha" in json.dumps(body).lower()


class BotStateStore:
    """Loads/persists watermarks to a dedicated ``bot-state`` branch + local cache."""

    def __init__(
        self,
        api: GitHubAPI,
        owner: str,
        repo: str,
        *,
        branch: str = BOT_STATE_BRANCH,
        path: str = BOT_STATE_PATH,
        local_path: Path = WATERMARKS_PATH,
    ) -> None:
        self.api = api
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.path = path
        self.local_path = local_path
        # Cached blob SHA of the remote file, used as the update precondition on
        # the next push (avoids a lost-update race + an extra GET).
        self._remote_blob_sha: str | None = None
        # B2: the canonical payload last known to be on the bot-state branch.
        # save() skips the remote commit when the new payload is byte-identical,
        # so a steady-state tick (no watermark advance) does NOT write a commit.
        # Without this the branch took one commit PER repo PER tick (~1,440/day),
        # growing unbounded. None = unknown → always write (fail safe).
        self._last_saved_payload: str | None = None

    def load(self) -> dict[str, Any]:
        """Load watermarks from the bot-state branch (source of truth).

        Ensures the branch exists (creates it from ``main`` HEAD if absent).
        Only ONE case is "empty is fine": the bot-state branch did **not** exist
        before this tick (a legitimate first-ever run) — then it is created and
        the state starts **empty** (it deliberately does NOT seed from the local
        cache; see below). If the branch ALREADY existed but the state file
        cannot be read (genuinely absent / unreadable as base64), that is **not**
        a fresh deployment — it is a misconfiguration that would silently disable
        L2/L5 by re-bootstrap, so it fails closed (raises). A transient remote
        error raises (the tick fails non-zero and retries next cron) and a
        *corrupt* remote payload likewise fails closed — none may degrade into an
        empty re-bootstrap. If the branch is absent and **cannot be created**
        (e.g. the App lacks ``contents: write``), durable persistence is
        impossible, so this also fails closed with an actionable message rather
        than limping on with a broken (cold-starting) state store."""
        branch_sha = self.api.get_ref_sha(self.owner, self.repo, self.branch)
        branch_existed = branch_sha is not None
        if branch_sha is None:
            # The durable source of truth (the branch) is absent. A LOCAL CACHE
            # present here is anomalous and must be resolved BEFORE we create the
            # branch (creating it then failing would leave a created-but-empty
            # branch that wedges every later tick on the "file missing" check).
            # Two ways this happens, neither safe to guess between from the file
            # alone:
            #   * disaster — the bot-state branch was deleted while a GOOD local
            #     mirror survived; trusting nothing silently skips the interval
            #     that mirror covered; or
            #   * a reused (self-hosted) workspace leaked STALE state from a run
            #     whose durable push failed — trusting it risks re-flooding on a
            #     stale / null entry (the production incident).
            # Fail closed and let a human decide. The cron clears this cache
            # before each tick, so on a healthy deployment this is unreachable;
            # reaching it means the workspace was NOT cleaned.
            if self.local_path.exists():
                raise RuntimeError(
                    f"bot-state branch {self.owner}/{self.repo}@{self.branch} is "
                    f"absent but a local watermark cache exists at "
                    f"{self.local_path} (fail-closed): refusing to silently "
                    f"bootstrap-to-HEAD (which would skip any interval the cache "
                    f"covered) or trust a possibly-stale cache (which could "
                    f"re-flood). Manual recovery: restore the bot-state branch, "
                    f"or delete {self.local_path} to force a clean re-bootstrap."
                )
            # First ever run for this deployment: create the dedicated branch
            # off main HEAD so subsequent saves have a ref to write to.
            head = self.api.main_head_sha(self.owner, self.repo)
            try:
                self.api.create_ref(self.owner, self.repo, self.branch, head)
                logger.info(
                    "created bot-state branch %s/%s@%s off main HEAD %s",
                    self.owner, self.repo, self.branch, head[:7],
                )
            except Exception as e:
                # Tolerate ONLY a genuine race: a concurrent tick created the
                # branch between our check and create, so the ref now resolves.
                # Anything else means durable persistence did not happen this
                # tick, so EVERY tick would cold-start and re-flood incidents
                # (the exact production failure). Swallowing it here as a "race"
                # is what hid that breakage; fail the tick LOUDLY instead.
                # The re-check is itself best-effort: a flaky re-check must not
                # mask the original create failure, so treat its error as "still
                # absent" and surface the create cause.
                try:
                    raced_sha = self.api.get_ref_sha(
                        self.owner, self.repo, self.branch
                    )
                except Exception:
                    raced_sha = None
                if raced_sha is not None:
                    # Genuine race: the branch exists now but was NOT there
                    # before this tick, so it is still morally a first run
                    # (branch_existed stays False → start empty below, and this
                    # tick writes the file).
                    logger.warning(
                        "bot-state branch create raced (now exists at %s): %s",
                        raced_sha[:7], e,
                    )
                elif isinstance(e, SecondaryRateLimitError):
                    # Transient throttle — fail the tick (it retries next cron);
                    # do NOT mis-blame permissions. Propagate the typed error.
                    raise
                elif getattr(getattr(e, "response", None), "status_code", None) == 403:
                    # A real missing-scope 403 (not a rate-limit 403, which
                    # ``_request`` would have raised as SecondaryRateLimitError):
                    # the App lacks ``contents: write`` so persistence can NEVER
                    # work. This never succeeds on retry — say so precisely.
                    raise RuntimeError(
                        f"cannot create the bot-state branch "
                        f"{self.owner}/{self.repo}@{self.branch}: the App "
                        f"appears to lack `contents: write`, so durable watermark "
                        f"persistence is impossible and the engine would "
                        f"cold-start (and re-flood incidents) every tick. Grant "
                        f"the App `contents: write` on {self.owner}/{self.repo}. "
                        f"Failing closed. Cause: {e}"
                    ) from e
                else:
                    # Other failure (transient 5xx/network, or an unexpected
                    # status). Fail closed; the next cron tick retries. If it
                    # persists, the App's contents:write / the ref API is the
                    # first thing to check.
                    raise RuntimeError(
                        f"cannot create the bot-state branch "
                        f"{self.owner}/{self.repo}@{self.branch} (failing closed; "
                        f"the tick retries next cron). If this persists, verify "
                        f"the App's `contents: write` permission. Cause: {e}"
                    ) from e
            self._remote_blob_sha = None

        found = self.api.get_file_on_ref(
            self.owner, self.repo, self.path, self.branch
        )
        if found is None:
            if branch_existed:
                # The bot-state branch was already there, but the state file is
                # missing / unreadable — NOT a fresh deployment. Re-bootstrapping
                # to HEAD here would silently disable L2/L5 every tick, so fail
                # the tick closed instead of returning empty.
                raise RuntimeError(
                    f"bot-state branch {self.owner}/{self.repo}@{self.branch} "
                    f"exists but its state file {self.path} is missing or "
                    f"unreadable (fail-closed): refusing to re-bootstrap to "
                    f"HEAD, which would disable L2/L5 post-merge re-validation."
                )
            # Branch did not exist before this tick (genuine clean first run; the
            # branch-absent + local-cache anomaly was already fail-closed above).
            # Start EMPTY — deliberately do NOT seed from any cache. bootstrap-to-
            # HEAD sets fresh per-repo watermarks this tick, and this tick's
            # save() writes them to the freshly-created branch.
            self._remote_blob_sha = None
            return {}

        text, blob_sha = found
        self._remote_blob_sha = blob_sha
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"bot-state watermark JSON corrupt on {self.owner}/{self.repo}@"
                f"{self.branch}:{self.path} (fail-closed): {e}"
            ) from e
        if not isinstance(data, dict):
            raise RuntimeError(
                f"bot-state watermark on {self.branch}:{self.path} is not an object"
            )
        # Mirror into the local cache so an in-tick crash still has the latest.
        try:
            save_watermarks(data, self.local_path)
        except OSError as e:
            logger.warning("could not seed local watermark cache: %s", e)
        # B2: remember the exact payload now on the branch (same canonical form
        # save() produces) so an unchanged tick skips the redundant commit.
        self._last_saved_payload = json.dumps(data, indent=2, sort_keys=True)
        return data

    def save(self, watermarks: dict[str, Any]) -> None:
        """Persist to the local cache (always) and the bot-state branch.

        The local write is atomic and best-effort-first so a remote failure
        still leaves the freshest state on disk for the upload artifact.

        A *transient / race* push failure (a stale-precondition 422 because a
        concurrent tick advanced the file, or a secondary-rate-limit) is
        survivable: the cached blob SHA is dropped so the next save re-reads and
        retries cleanly, and this push is skipped (the next push re-sends the
        full state). But a *hard* push failure — missing permission (403, e.g.
        no ``contents:write``) or any other non-transient error — **raises**:
        a silently-swallowed persistent save failure would cold-start L2/L5
        every tick forever, so the tick must fail closed instead."""
        try:
            save_watermarks(watermarks, self.local_path)
        except OSError as e:
            logger.error("local watermark cache write failed: %s", e)

        payload = json.dumps(watermarks, indent=2, sort_keys=True)
        # B2: skip the remote commit when nothing changed. The local cache was
        # written above (cheap, always); only the durable branch write is
        # conditional. A steady-state tick (no watermark advance) thus produces
        # ZERO bot-state commits instead of one per repo — the branch grows with
        # real activity, not wall-clock. ``None`` (first save on a fresh branch,
        # or after a dropped precondition) always writes (fail safe).
        if self._last_saved_payload is not None and payload == self._last_saved_payload:
            return
        try:
            if self._remote_blob_sha is None:
                # No cached precondition (first save on a fresh deployment, or a
                # prior failed/raced push dropped it). Re-read the current blob
                # SHA: updating an EXISTING file without ``sha`` is a guaranteed
                # 422, which would otherwise wedge every save for the rest of
                # the tick. This is the re-read the failure path below relies on.
                found = self.api.get_file_on_ref(
                    self.owner, self.repo, self.path, self.branch
                )
                if found is not None:
                    self._remote_blob_sha = found[1]
            new_sha = self.api.put_file_on_ref(
                self.owner,
                self.repo,
                self.path,
                ref=self.branch,
                content=payload,
                message="chore(bot-state): update branch_supervisor watermarks",
                blob_sha=self._remote_blob_sha,
            )
            self._remote_blob_sha = new_sha or self._remote_blob_sha
            # B2: the branch now holds this payload — a later unchanged save skips.
            self._last_saved_payload = payload
        except Exception as e:
            # Drop the cached blob sha so the next save re-reads and retries
            # cleanly regardless of cause.
            self._remote_blob_sha = None
            if _is_transient_push_error(e):
                # Stale precondition (someone/something advanced the file) or a
                # secondary-rate-limit: survivable — log and keep going.
                logger.error(
                    "durable watermark push to %s/%s@%s failed (transient, "
                    "retry next tick): %s",
                    self.owner, self.repo, self.branch, e,
                )
                return
            # Hard failure (missing permission / non-transient): fail closed so
            # the tick does not silently cold-start forever.
            logger.error(
                "durable watermark push to %s/%s@%s failed (hard, failing "
                "closed): %s",
                self.owner, self.repo, self.branch, e,
            )
            raise
