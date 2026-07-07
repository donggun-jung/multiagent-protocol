"""Cron entry point — one tick.

The orchestrator (``docs/concepts/architecture.md``). It wires the modules
together but contains no enforcement logic of its own:

1. Load config + skills; authenticate as the GitHub App.
2. For each installation, for each supervised repo it owns:
   - per open PR: ``runtime.process_pr`` (L1/L3/L4 → merge / inbox / comment).
   - L5 break-glass + hallucination scan (``branch_supervisor.scan_repo``).
   - L2 post-merge re-validation (``branch_supervisor.revalidate_main``).
3. For the installation that owns the governance repo: mirror drift check +
   Decision Inbox poll (apply owner verdicts).
4. Persist watermarks; log tick metrics.

Incidents are opened idempotently (any issue — open OR closed — referencing
the same dedupe key is not re-created, so a human-closed diagnostic stays
closed), so the tick is safe to run every 5 minutes even though the bot is
stateless across ticks.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from multiagent_protocol.auth import AppAuth
from multiagent_protocol.auto_revert import ensure_revert_pr
from multiagent_protocol.branch_supervisor import (
    BotStateStore,
    SupervisorIncident,
    bootstrap_watermark_if_absent,
    count_l2_unsettled,
    revalidate_main,
    scan_repo,
)
from multiagent_protocol.config.loader import load_config
from multiagent_protocol.decision_inbox import resolve_open_issues
from multiagent_protocol.drift_check import (
    DriftIncident,
    check_repo_against_canonical,
    incidents_to_issue_body,
    load_mirror_config,
)
from multiagent_protocol.github_api import GitHubAPI, SecondaryRateLimitError
from multiagent_protocol.l4_burn_in import L4BurnInStore, apply_burn_in
from multiagent_protocol.runtime import (
    build_branch_hooks,
    build_runtime_skills,
    process_pr,
)
from multiagent_protocol.skills.builtin.hook_unauthorized_push import (
    INCIDENT_LABEL as UNAUTHORIZED_PUSH_LABEL,
)
from multiagent_protocol.skills.builtin.validator_ci_green import (
    DEFAULT_CHECK_PUBLISHER,
)

logger = logging.getLogger(__name__)

AUDIT_LOG = Path("bot-state/classifier_audit.jsonl")
MIRROR_PATHS = Path("schemas/mirror_paths.json")

# Stable, machine-greppable marker embedded in every incident issue so dedupe
# survives the issue body being edited by a human.
_DEDUPE_MARKER = "<!-- merge-gate:dedupe:{key} -->"

# Hard cap on NEW incident issues opened per tick (across all repos). A backstop
# against a pathological tick (e.g. a mass force-push, or a config error)
# opening hundreds of issues. Beyond the cap the tick logs the overflow and
# defers — the watermark does NOT advance past un-surfaced incidents, so they
# are re-attempted next tick rather than lost.
MAX_ISSUES_PER_TICK = 30

# Stop opening new L5 break-glass/unauthorized issues below this primary-rate
# reserve, and end the tick's expensive drift loop early — leaving headroom to
# always reach the persistence step.
RATE_LIMIT_RESERVE = 200


def _has_secrets(env) -> bool:
    return bool(env.get("MERGE_GATE_APP_ID") and env.get("MERGE_GATE_PRIVATE_KEY"))


def _open_incident_if_new(
    api: GitHubAPI, gov_owner: str, gov_repo: str,
    label: str, body: str, dedupe_key: str,
) -> bool:
    """Open an incident issue unless one already references dedupe_key.

    Dedupe is against ``state="all"`` (open AND closed), keyed on a stable
    marker (the dedupe_key, e.g. the commit SHA) embedded in the issue
    title/body. Deduping only against OPEN issues created a zombie loop: closing
    a false incident let the very next tick reopen it. A closed incident is a
    deliberate human "resolved/won't-fix" signal and must stay closed."""
    marker = _DEDUPE_MARKER.format(key=dedupe_key)
    try:
        existing = api.list_issues(gov_owner, gov_repo, labels=label, state="all")
    except Exception as e:
        logger.error("could not list issues for dedupe (%s): %s", label, e)
        existing = []
    for issue in existing:
        haystack = f"{issue.get('title') or ''}\n{issue.get('body') or ''}"
        if marker in haystack or dedupe_key in haystack:
            return False
    api.open_issue(
        owner=gov_owner, repo=gov_repo,
        title=f"[{label}] {dedupe_key}",
        body=f"{body}\n\n{marker}",
        labels=[label],
    )
    return True


# The L2 real-failure incident label — the one path FEATURE A augments with a
# revert PR. Kept as a constant so the auto-revert dispatch matches exactly.
L2_REVALIDATION_LABEL = "decision:post-merge-revalidation"


def _issue_ref(issue: dict) -> str:
    """A ``Task-Ref``-shaped reference for an incident issue (``Issue#N``)."""
    return f"Issue#{issue.get('number')}"


def _installation_token(api: GitHubAPI) -> str | None:
    """Best-effort installation token for the given repo's client (auto-revert
    clone/push auth). Returns None when the client has no App auth (a test
    double) or the exchange fails — the auto-revert then degrades to
    incident-only rather than crashing the tick."""
    auth = getattr(api, "auth", None)
    inst_id = getattr(api, "installation_id", None)
    if auth is None or inst_id is None:
        return None
    try:
        return auth.installation_token(inst_id)
    except Exception as e:  # noqa: BLE001 - fail-safe → incident-only
        logger.warning("auto-revert: could not obtain installation token: %s", e)
        return None


def _append_incident_note(
    api: GitHubAPI, gov_owner: str, gov_repo: str,
    issue: dict, marker: str, note: str,
) -> None:
    """Append the auto-revert note to an incident issue body (idempotent).

    The note is inserted just before the hidden dedupe marker so the marker
    stays at the end (where dedupe/greppers expect it). A no-op when the note is
    already present, so a re-emitted incident (tick died pre-persist) does not
    stack duplicate notes."""
    number = issue.get("number")
    if number is None or not note:
        return
    body = issue.get("body") or ""
    if note in body:
        return
    if marker in body:
        body = body.replace(marker, f"{note}\n\n{marker}", 1)
    else:
        body = f"{body}\n\n{note}"
    try:
        api.update_issue_body(gov_owner, gov_repo, number, body)
        issue["body"] = body  # keep the in-memory copy consistent this tick
    except Exception as e:  # noqa: BLE001 - the incident already exists; note is best-effort
        logger.warning(
            "auto-revert: could not append revert note to issue #%s: %s", number, e
        )


def _suppress_false_unauthorized(
    api: GitHubAPI,
    owner: str,
    repo: str,
    incidents: list[SupervisorIncident],
    allowlisted: tuple[str, ...],
) -> list[SupervisorIncident]:
    """Drop unauthorized-push incidents whose TRUE merge actor is allowlisted.

    A squash/rebase merge lands on ``main`` with a ``web-flow``/App committer,
    so ``hook_unauthorized_push`` (which only sees commit metadata) can flag a
    perfectly legitimate merge that an allowlisted human clicked. Here at the
    call site — WITHOUT touching the hook's committer-identity trust logic — we
    resolve the real ``merged_by`` via the commit→PR endpoint and suppress the
    incident when that actor is allowlisted. Non-unauthorized incidents pass
    through untouched; a resolution failure is fail-closed (keep the incident).
    """
    if not allowlisted:
        return incidents
    kept: list[SupervisorIncident] = []
    for inc in incidents:
        if inc.label != UNAUTHORIZED_PUSH_LABEL:
            kept.append(inc)
            continue
        try:
            merger = api.commit_merged_by(owner, repo, inc.commit_sha)
        except Exception as e:
            logger.warning(
                "merged_by lookup failed for %s/%s@%s (keeping incident): %s",
                owner, repo, inc.commit_sha[:7], e,
            )
            kept.append(inc)
            continue
        if merger is not None and merger in allowlisted:
            logger.info(
                "suppressing unauthorized-push incident on %s/%s@%s — true "
                "merger %r is allowlisted (web-flow committer masked it)",
                owner, repo, inc.commit_sha[:7], merger,
            )
            continue
        kept.append(inc)
    return kept


def _rollup_incidents(
    owner: str, repo: str, incidents: list[SupervisorIncident]
) -> list[tuple[str, str, str]]:
    """Collapse per-commit L5 incidents into ONE rollup per (repo, label).

    Returns ``(label, dedupe_key, body)`` tuples. When a label has several
    offending commits this tick, they become a single rollup issue listing every
    SHA — instead of one issue per commit (the flood). The dedupe key is keyed on
    the SORTED SHA SET, so the same set of offenders persisting across ticks
    dedupes to the same issue (idempotent), while a NEW offender produces a fresh
    rollup. A single-offender label degrades gracefully to a one-SHA issue."""
    by_label: dict[str, list[SupervisorIncident]] = {}
    for inc in incidents:
        by_label.setdefault(inc.label, []).append(inc)

    out: list[tuple[str, str, str]] = []
    for label, items in by_label.items():
        shas = sorted({i.commit_sha for i in items})
        if len(shas) == 1:
            # One offender — keep the rich per-commit body + SHA dedupe key.
            out.append((label, shas[0][:7], items[0].body))
            continue
        set_hash = hashlib.sha256("\n".join(shas).encode()).hexdigest()[:12]
        dedupe_key = f"rollup-{repo}-{set_hash}"
        listing = "\n".join(f"- `{s[:12]}`" for s in shas)
        bodies = "\n\n".join(
            f"### `{i.commit_sha[:7]}`\n{i.body}" for i in items
        )
        body = (
            f"**{len(shas)} `{label}` commits on `{owner}/{repo}` this tick.**\n\n"
            f"Offending SHAs:\n{listing}\n\n"
            f"This rollup replaces one-issue-per-commit to avoid an incident "
            f"flood. Details per commit below.\n\n---\n\n{bodies}"
        )
        out.append((label, dedupe_key, body))
    return out


def _drift_dedupe_key(drift: list[DriftIncident]) -> str:
    """Content-derived dedupe key for one tick's drift findings.

    The key must change when the drift STATE changes. With ``state="all"``
    dedupe, a constant key (the old ``"mirror-drift"``) would mean the first
    closed drift issue suppresses every future drift report forever — a silent
    loss. Keying on the sorted findings set keeps the closed-stays-closed
    property for the *same* unresolved state while letting any new/changed
    drift open a fresh issue."""
    canon = "\n".join(sorted(
        f"{i.adopter_full_name}|{i.path}|{i.kind}|{i.canonical_sha}|{i.adopter_sha}"
        for i in drift
    ))
    return "mirror-drift-" + hashlib.sha256(canon.encode()).hexdigest()[:12]


class _DriftTreeAPI:
    """Drift-scoped read façade: one tree fetch per repo per tick, blob-SHA equality.

    ``check_repo_against_canonical`` asks for ``get_file_sha256(owner, repo,
    path)`` once per canonical path per adopter — with the governance repo
    re-hashed for EVERY adopter, the dominant drift cost. This façade answers
    those calls from a single ``git/trees?recursive=1`` fetch per repo, cached
    for the tick (so canonical hashes are computed once, not per-adopter), and
    compares git blob SHAs instead of downloading file bodies. When a tree is
    unavailable (404 / truncated giant repo) it falls back to the real per-path
    lookup — which now returns the SAME blob-SHA value kind, so the fast path
    and the fallback can never disagree about equality."""

    def __init__(self, api: GitHubAPI) -> None:
        self._api = api
        self._trees: dict[tuple[str, str], dict[str, str] | None] = {}

    def get_file_sha256(self, owner: str, repo: str, path: str) -> str | None:
        key = (owner, repo)
        if key not in self._trees:
            try:
                self._trees[key] = self._api.get_tree_blob_shas(owner, repo)
            except Exception as e:
                logger.warning(
                    "tree fetch failed for %s/%s (per-path fallback): %s",
                    owner, repo, e,
                )
                self._trees[key] = None
        tree = self._trees[key]
        if tree is None:
            return self._api.get_file_sha256(owner, repo, path)
        return tree.get(path)


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Single tick clock, injectable for tests (FEATURE B — L4 burn-in). Never
    # read datetime.now() inline in the burn-in logic that tests cover.
    now = now or datetime.now(timezone.utc)

    config_dir = Path("config")
    schemas_dir = Path("schemas")
    # config/ always contains a tracked README.md placeholder, so the directory
    # exists even on the framework upstream. A real *deployment* is signalled by
    # the actual YAML config files (owner/projects/env), which are git-ignored
    # and only present once someone runs the wizard and commits their config.
    has_config = any(
        (config_dir / name).exists()
        for name in ("owner.yml", "projects.yml", "env.yml")
    )
    has_secrets = _has_secrets(os.environ)

    # No App credentials. Two distinct cases:
    #  - No deployment config either → the PUBLIC framework upstream (or a fresh
    #    fork), not a running deployment. Exit 0 so the scheduled tick does not
    #    fail every 5 minutes.
    #  - Deployment config IS present → a deployment that is missing its secrets.
    #    That is a misconfiguration; a "bot-only merge gate" should FAIL LOUDLY
    #    (exit non-zero) rather than show a green check while gating nothing.
    if not has_secrets:
        if has_config:
            logger.error(
                "config/ holds a deployment config but MERGE_GATE_APP_ID / "
                "MERGE_GATE_PRIVATE_KEY are not set — the gate is NOT running. "
                "Add the Actions secrets (see docs/guide/quick-start.md)."
            )
            return 1
        logger.info(
            "no App credentials and no deployment config — this is the framework "
            "upstream, not a deployment. Nothing to do; exiting cleanly."
        )
        return 0

    if not has_config:
        logger.error(
            "no deployment config found under config/ (need owner.yml, "
            "projects.yml, env.yml). Run the wizard first."
        )
        return 2
    try:
        config = load_config(config_dir, schemas_dir if schemas_dir.exists() else None)
    except Exception as e:
        logger.error("config load failed: %s", e)
        return 2

    logger.info(
        "config: governance=%s, supervised=%d, runner_tier=%s",
        config.projects.governance_repo,
        len(config.projects.supervised_repos),
        config.env.runner_tier,
    )

    try:
        auth = AppAuth.from_env()
        installations = auth.installations()
    except Exception as e:
        logger.error("auth / installation discovery failed: %s", e)
        return 3
    logger.info("found %d installation(s)", len(installations))

    gov_owner, _, gov_repo = config.projects.governance_repo.partition("/")
    # Decision Inbox issues live in decision_inbox.repository (defaults to the
    # governance repo); incidents (drift/break-glass/post-merge) stay in gov.
    inbox_owner, _, inbox_repo = config.projects.effective_inbox_repository.partition("/")
    supervised = list(config.projects.supervised_repos)
    allowlisted = config.owner.allowlisted_actors
    metrics: dict[str, int] = {
        "merged": 0, "observe": 0, "inbox": 0, "blocked": 0, "race-rebased": 0,
        "l5_incidents": 0, "l2_incidents": 0, "drift_incidents": 0,
        "inbox_resolved": 0, "l2_unsettled": 0, "issues_deferred": 0,
        "bootstrapped": 0, "auto_revert_prs": 0, "l4_promoted": 0,
    }

    # Durable watermark state lives on a dedicated bot-state branch of the
    # governance repo (NOT main), written via the App's contents:write. Build
    # the store on the governance installation's client and LOAD the watermark
    # from the branch (source of truth) at tick start. A corrupt persisted state
    # fails closed here (raises → non-zero tick) rather than silently re-flooding.
    gov_install = next(
        (i for i in installations if (i.get("account") or {}).get("login") == gov_owner),
        None,
    )
    if gov_install is None:
        logger.error(
            "no App installation found for governance account %r — cannot "
            "load/persist durable watermarks", gov_owner,
        )
        return 3
    gov_api = GitHubAPI(auth, gov_install["id"])
    store = BotStateStore(gov_api, gov_owner, gov_repo)
    watermarks = store.load()
    # FEATURE B — L4 burn-in clock. Its state file lives on the SAME bot-state
    # branch (guaranteed to exist now that store.load() ran). Fail-safe reads,
    # best-effort writes; disabled by default (l4_burn_in_days=0 → inert).
    burn_in_store = L4BurnInStore(gov_api, gov_owner, gov_repo)

    def _persist() -> None:
        # ``store.save`` already swallows transient/race push failures (stale
        # 422, secondary-rate-limit) internally; anything it RAISES is a hard,
        # non-transient failure (e.g. missing ``contents:write`` / 403). That
        # must fail the tick closed — a silently-swallowed persistent save
        # failure would cold-start L2/L5 every tick forever — so it propagates.
        store.save(watermarks)

    # A per-tick issue-creation budget (a backstop against a mass-incident
    # flood). ``_issue_budget`` is a single-element list so the nested helper can
    # mutate it. When exhausted, incidents are deferred (not lost): the watermark
    # is not advanced past an un-surfaced incident, so it is re-attempted later.
    issue_budget = [MAX_ISSUES_PER_TICK]

    def _open_capped(label: str, body: str, dedupe_key: str, metric: str) -> bool:
        """Open via dedupe within the per-tick budget.

        Returns False ONLY when the cap deferred a new issue — the caller must
        then hold the repo's watermark so the incident is regenerated and
        re-attempted next tick (already-opened ones dedupe) instead of being
        silently lost. A deduped incident returns True: it is already
        surfaced; there is nothing to re-attempt."""
        if issue_budget[0] <= 0:
            metrics["issues_deferred"] += 1
            logger.warning(
                "per-tick issue cap (%d) reached — deferring %s %s",
                MAX_ISSUES_PER_TICK, label, dedupe_key,
            )
            return False
        if _open_incident_if_new(gov_api, gov_owner, gov_repo, label, body, dedupe_key):
            issue_budget[0] -= 1
            metrics[metric] += 1
        return True

    def _open_l2_capped(repo_api, owner: str, name: str, inc) -> bool:
        """L2 incident opener that also runs FEATURE A (auto-revert) when on.

        For a ``decision:post-merge-revalidation`` incident with
        ``auto_revert_pr`` enabled: open the incident FIRST (its number becomes
        the revert commit's ``Task-Ref``), then create/link the revert PR in the
        SUPERVISED repo (``repo_api`` — its own installation client), then append
        the PR link (or failure reason) to the incident body. Everything else —
        and the disabled case — falls straight through to :func:`_open_capped`,
        so the well-tested flood-control / budget path is unchanged.

        Returns False ONLY when the per-tick cap deferred a NEW issue (caller
        holds the watermark), matching ``_open_capped``.
        """
        if not (config.env.auto_revert_pr and inc.label == L2_REVALIDATION_LABEL):
            return _open_capped(inc.label, inc.body, inc.commit_sha[:7], "l2_incidents")

        dedupe_key = inc.commit_sha[:7]
        marker = _DEDUPE_MARKER.format(key=dedupe_key)
        # Does the incident already exist (open OR closed)? If so we do NOT spend
        # budget, but we DO still (idempotently) ensure the revert PR — a prior
        # tick may have opened the issue then died before pushing the branch.
        try:
            existing = gov_api.list_issues(
                gov_owner, gov_repo, labels=inc.label, state="all"
            )
        except Exception as e:
            logger.error("could not list issues for dedupe (%s): %s", inc.label, e)
            existing = []
        match = None
        for issue in existing:
            hay = f"{issue.get('title') or ''}\n{issue.get('body') or ''}"
            if marker in hay or dedupe_key in hay:
                match = issue
                break

        if match is None:
            # A NEW incident — subject to the per-tick cap, exactly like _open_capped.
            if issue_budget[0] <= 0:
                metrics["issues_deferred"] += 1
                logger.warning(
                    "per-tick issue cap (%d) reached — deferring %s %s",
                    MAX_ISSUES_PER_TICK, inc.label, dedupe_key,
                )
                return False
            match = gov_api.open_issue(
                owner=gov_owner, repo=gov_repo,
                title=f"[{inc.label}] {dedupe_key}",
                body=f"{inc.body}\n\n{marker}",
                labels=[inc.label],
            )
            issue_budget[0] -= 1
            metrics["l2_incidents"] += 1

        # Create / link the revert PR (never raises), then append the note to
        # the incident body. Token comes from the supervised repo's own
        # installation client so the clone/push is authorised for that repo.
        token = _installation_token(repo_api)
        result = ensure_revert_pr(
            repo_api, owner, name, inc.commit_sha,
            token=token, incident_ref=_issue_ref(match),
        )
        if result.created:
            metrics["auto_revert_prs"] += 1
        _append_incident_note(gov_api, gov_owner, gov_repo, match, marker, result.note)
        return True

    try:
        for inst in installations:
            account = (inst.get("account") or {}).get("login")
            api = gov_api if account == gov_owner else GitHubAPI(auth, inst["id"])
            # build_runtime_skills resolves the bot identity via GET /app, which
            # can transiently raise (e.g. a flaky /app while resolving the App
            # slug). That hiccup is fail-closed for THIS installation (no
            # runtime → no gating, no merge), but it must NOT abort the whole
            # tick and starve the healthy installations — log and move on.
            try:
                runtime = build_runtime_skills(config, api, config_dir=config_dir)
            except Exception as e:
                logger.error(
                    "skipping installation %r — runtime build failed "
                    "(fail-closed, no merge for this installation): %s",
                    account, e,
                )
                continue

            # FEATURE B — L4 burn-in: promote validator_agent_registry from
            # advisory (P2) to hard-block (P0) once the window elapses, mutating
            # this installation's runtime IN PLACE before any PR is gated. Inert
            # unless l4_burn_in_days>0; the operator's severity_overrides always
            # wins. Never raises.
            try:
                burn = apply_burn_in(runtime, config, burn_in_store, now=now)
                if burn.just_promoted:
                    metrics["l4_promoted"] += 1
                    logger.info(
                        "L4 burn-in: %s (installation %r)", burn.reason, account
                    )
            except Exception as e:  # noqa: BLE001 - burn-in must never abort a tick
                logger.error("L4 burn-in evaluation failed (advisory stays): %s", e)

            for full in [r for r in supervised if r.split("/")[0] == account]:
                owner, _, name = full.partition("/")
                # DEC-C: an audit-only repo is scanned on main (L2 + L5 + the
                # unauthorized-push detector) but its open PRs are NOT gated —
                # L1-L4 is skipped. This lets the governance repo be supervised
                # without the self-gating paradox. Default: no repo is audit-only.
                audit_only = config.projects.is_audit_only(full)

                # L1 + L3 + L4 per open PR (skipped for audit-only repos).
                if audit_only:
                    logger.info("repo %s is audit-only — skipping L1-L4 gating", full)
                else:
                    try:
                        prs = api.list_open_prs(owner, name)
                    except Exception as e:
                        logger.error("list PRs failed for %s: %s", full, e)
                        prs = []
                    for pr_payload in prs:
                        number = pr_payload.get("number")
                        try:
                            d = process_pr(
                                api, config, runtime, pr_payload,
                                audit_log_path=AUDIT_LOG,
                            )
                            metrics[d.action] = metrics.get(d.action, 0) + 1
                            logger.info(
                                "PR %s#%s → %s (Q%s) %s",
                                full, number, d.action, d.quadrant, d.detail,
                            )
                        except Exception as e:
                            logger.error("process PR %s#%s failed: %s", full, number, e)

                # BOOTSTRAP-TO-HEAD: the first time a repo+layer is seen, set its
                # watermark to current main HEAD, persist, and scan NOTHING this
                # tick — gate doctrine cannot apply to pre-activation history,
                # and a cold rewalk is what caused the live L5 flood + timeout.
                l2_required = config.projects.effective_required_checks(
                    full, config.env.required_checks
                )
                try:
                    boot_l5 = bootstrap_watermark_if_absent(api, owner, name, watermarks)
                    boot_l2 = bootstrap_watermark_if_absent(
                        api, owner, name, watermarks, key_suffix=":l2"
                    )
                    if boot_l5 is not None or boot_l2 is not None:
                        metrics["bootstrapped"] += 1
                        _persist()  # persist the new HEAD watermark immediately

                    # L5 break-glass + hallucination + unauthorized-push scan.
                    if boot_l5 is None:
                        incidents, wm = scan_repo(
                            api, owner, name,
                            build_branch_hooks(runtime, api, owner, name),
                            watermarks,
                        )
                        incidents = _suppress_false_unauthorized(
                            api, owner, name, incidents, allowlisted
                        )
                        l5_deferred = False
                        for label, key, body in _rollup_incidents(owner, name, incidents):
                            if not _open_capped(label, body, key, "l5_incidents"):
                                l5_deferred = True
                        # A deferred (capped) incident must not be lost: hold the
                        # watermark so next tick re-scans this span and re-attempts
                        # it; already-opened issues dedupe by their stable keys.
                        if wm and not l5_deferred:
                            watermarks[f"{owner}/{name}"] = wm

                    # L2 post-merge re-validation (R1 effective required_checks).
                    if boot_l2 is None:
                        l2_incidents, l2_wm = revalidate_main(
                            api, owner, name, l2_required, watermarks,
                            allow_no_ci=config.env.allow_no_ci,
                            expected_check_publisher=(
                                config.projects.effective_expected_check_publisher(
                                    full, config.env.expected_check_publisher
                                )
                                or DEFAULT_CHECK_PUBLISHER
                            ),
                        )
                        l2_deferred = False
                        for inc in l2_incidents:
                            # FEATURE A: on a real-failure incident with
                            # auto_revert_pr on, this also opens/links a revert
                            # PR in the supervised repo and links it in the
                            # incident. Otherwise identical to _open_capped.
                            if not _open_l2_capped(api, owner, name, inc):
                                l2_deferred = True
                        # Same hold-on-deferral rule. (Corner: a deferred STALL
                        # incident re-tracks with a fresh first-seen next tick —
                        # an extra grace period, never a lost incident.)
                        if l2_wm and not l2_deferred:
                            watermarks[f"{owner}/{name}:l2"] = l2_wm
                except SecondaryRateLimitError as e:
                    # One throttled repo must not abort the whole tick and replay
                    # forever — log, persist progress, move to the next repo.
                    logger.error("rate-limited scanning %s; skipping repo: %s", full, e)
                except Exception as e:
                    logger.error("supervisor scan failed for %s: %s", full, e)

                # Incremental persistence: save after EACH repo so a 5-min
                # timeout mid-fleet still banks the watermarks advanced so far.
                _persist()

                if (
                    api.rate_limit_remaining is not None
                    and api.rate_limit_remaining < RATE_LIMIT_RESERVE
                ):
                    logger.warning(
                        "rate-limit reserve hit (%s remaining) — ending tick "
                        "early after %s to guarantee persistence",
                        api.rate_limit_remaining, full,
                    )
                    break

            # Governance-scoped work runs under the installation that owns it.
            if account == gov_owner:
                _run_governance_work(
                    api, config, gov_owner, gov_repo, inbox_owner, inbox_repo,
                    supervised, allowlisted, metrics, _open_capped,
                )

        metrics["l2_unsettled"] = count_l2_unsettled(watermarks)
        logger.info(
            "rate-limit remaining at tick end: %s", gov_api.rate_limit_remaining
        )
    finally:
        # finally guard: even on a timeout/exception, bank the watermarks.
        _persist()

    logger.info("tick complete: %s", metrics)
    return 0


def _run_governance_work(
    api, config, gov_owner, gov_repo, inbox_owner, inbox_repo,
    supervised, allowlisted, metrics, open_capped,
) -> None:
    """Drift check + Decision-Inbox poll (governance installation only)."""
    if MIRROR_PATHS.exists():
        try:
            mirror = load_mirror_config(MIRROR_PATHS)
            # Per-tick tree cache: the governance (canonical) tree is fetched
            # ONCE and reused for every adopter; comparisons are git blob SHAs,
            # not downloaded file bodies.
            drift_api = _DriftTreeAPI(api)
            drift: list[DriftIncident] = []
            for full in [r for r in supervised if r.split("/")[0] == gov_owner]:
                a_owner, _, a_repo = full.partition("/")
                # Skip the governance/source repo: it IS the canonical, so
                # comparing it to itself is always-clean wasted API calls.
                if (a_owner, a_repo) == (gov_owner, gov_repo):
                    continue
                # Below the rate reserve, stop the expensive drift loop early
                # and let the tick reach persistence.
                if (
                    api.rate_limit_remaining is not None
                    and api.rate_limit_remaining < RATE_LIMIT_RESERVE
                ):
                    logger.warning(
                        "rate-limit reserve hit during drift loop (%s) — "
                        "ending drift scan early", api.rate_limit_remaining,
                    )
                    break
                drift += check_repo_against_canonical(
                    drift_api, gov_owner, gov_repo, a_owner, a_repo, mirror
                )
            if drift:
                open_capped(
                    "decision:mirror-drift-incident",
                    incidents_to_issue_body(drift),
                    _drift_dedupe_key(drift), "drift_incidents",
                )
        except Exception as e:
            logger.error("drift check failed: %s", e)

    try:
        resolutions = resolve_open_issues(
            api, inbox_owner, inbox_repo, allowlisted
        )
        metrics["inbox_resolved"] += len(resolutions)
        for r in resolutions:
            logger.info("inbox: %s#%s → %s (%s)",
                        r.pr_full_name, r.pr_number, r.verdict, r.action)
    except Exception as e:
        logger.error("inbox poll failed: %s", e)


if __name__ == "__main__":
    raise SystemExit(main())
