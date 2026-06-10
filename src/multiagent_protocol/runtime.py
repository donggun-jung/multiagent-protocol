"""Runtime assembly + per-PR processing.

Bridges the config layer and the skills layer into the concrete objects the
cron tick runs. ``main.py`` owns no enforcement logic itself
(``docs/concepts/architecture.md``); it calls into here.

- :func:`build_runtime_skills` constructs the **configured** built-in skills
  (injecting owner allowlist, publisher slug, agent registry, bot repo, ADR
  finder) and merges any user-added skills, then applies
  ``config.skills.disabled`` + ``severity_overrides``.
- :func:`build_branch_hooks` adds the per-repo hallucination resolver.
- :func:`process_pr` runs one PR through classify → L1/L3/L4 → decision and
  performs the resulting GitHub side effect (merge / inbox issue / comment).
  The merge itself is additionally gated by the ``MERGE_GATE_MERGE_ENABLED``
  env var (observe-only by default; see :func:`merge_enabled`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from multiagent_protocol.classifier import classify
from multiagent_protocol.config.loader import AppConfig
from multiagent_protocol.decision_inbox import (
    PENDING_LABEL,
    open_inbox_issue,
    parse_pr_ref,
)
from multiagent_protocol.github_api import GitHubAPI
from multiagent_protocol.label_provenance import (
    approval_receipt_comment,
    approval_receipt_times,
    approval_receipts,
    labels_needing_receipt,
)
from multiagent_protocol.pr_validator import (
    build_pr_context,
    evaluate_pr,
    run_l3_race_guard,
)
from multiagent_protocol.skills.builtin.classifier_auto_revert import (
    AutoRevertClassifier,
)
from multiagent_protocol.skills.builtin.classifier_bot_self_repo import (
    BotSelfRepoClassifier,
)
from multiagent_protocol.skills.builtin.classifier_empty_pr import EmptyPrClassifier
from multiagent_protocol.skills.builtin.classifier_path_default import (
    PathDefaultClassifier,
)
from multiagent_protocol.skills.builtin.classifier_published_verdict import (
    PublishedVerdictClassifier,
)
from multiagent_protocol.skills.builtin.hook_break_glass_audit import (
    BreakGlassAuditHook,
)
from multiagent_protocol.skills.builtin.hook_hallucination_guard import (
    HallucinationGuardHook,
)
from multiagent_protocol.skills.builtin.hook_unauthorized_push import (
    UnauthorizedPushHook,
)
from multiagent_protocol.skills.builtin.validator_agent_registry import (
    AgentRegistryValidator,
)
from multiagent_protocol.skills.builtin.validator_base_up_to_date import (
    BaseUpToDateValidator,
)
from multiagent_protocol.skills.builtin.validator_ci_green import (
    DEFAULT_CHECK_PUBLISHER,
    CiGreenValidator,
)
from multiagent_protocol.skills.builtin.validator_classifier_publisher import (
    ClassifierPublisherValidator,
)
from multiagent_protocol.skills.builtin.validator_owner_approval import (
    OwnerApprovalValidator,
)
from multiagent_protocol.skills.builtin.validator_ready_to_merge import (
    ReadyToMergeValidator,
)
from multiagent_protocol.skills.builtin.validator_trailers import TrailersValidator
from multiagent_protocol.skills.loader import load_user_skills

logger = logging.getLogger(__name__)

# Built-ins the operator may NOT disable via config/skills.yml. See
# docs/concepts/general-preferences.md ("not permitted via disabled:").
# schemas/skills.schema.json rejects these names in `disabled` at config-load
# time; this set is the belt-and-suspenders runtime enforcement for configs
# that bypass schema validation.
NON_DISABLEABLE = frozenset({
    "validator_trailers",
    "validator_classifier_publisher",
    "classifier_bot_self_repo",
    "hook_break_glass_audit",
    # R3: the code-level substitute for paid branch protection. If it could be
    # silently turned off via skills.disabled, a fleet with no paid branch
    # protection would have NOTHING watching main for unsanctioned writes — a
    # fail-open. It stays armed regardless of `disabled`.
    "hook_unauthorized_push",
    # The core L1 gate conditions (C1-C4). Previously only their SEVERITY was
    # clamped (CORE_L1_VALIDATORS below); a `disabled:` entry could remove
    # them entirely — letting a PR with red CI or no ready label auto-merge.
    # They now always run regardless of config. (validator_owner_approval/C3
    # is also constructed unconditionally per-PR in process_pr, so listing it
    # here keeps the declared policy complete.)
    "validator_ready_to_merge",
    "validator_ci_green",
    "validator_owner_approval",
    "validator_base_up_to_date",
})

# Core L1 validators whose severity must stay blocking (P0/P1). The operator
# may not downgrade them to warn/audit via severity_overrides — that would let
# a PR with no ready-to-merge label, or red CI, auto-merge as Quadrant A.
CORE_L1_VALIDATORS = frozenset({
    "validator_ready_to_merge",
    "validator_ci_green",
    "validator_owner_approval",
    "validator_base_up_to_date",
    "validator_trailers",
    "validator_classifier_publisher",
})
_BLOCKING_SEVERITIES = ("P0", "P1")

# Passive-audit issue labels for auto-approved B / C PRs (four-quadrants.md).
AUDIT_LABEL = {
    "B": "decision:auto-approved-critical-reversible",
    "C": "decision:auto-approved-irreversible-non-critical",
}

DIAGNOSTIC_PREFIX = "Merge Gate L1"

# Operational kill-switch (mirrors the old bot's toggle of the same name).
# Unless this env var is explicitly "true", process_pr runs in OBSERVE-ONLY
# mode: everything up to the merge (classify, L1, owner-approval / inbox
# routing, diagnostics, the L3 race guard) runs for real, but the merge itself
# — and with it the post-merge audit issue — is withheld and recorded as an
# "observe" decision. This lets a production deployment burn in (watch +
# report) before it is allowed to write to main.
MERGE_ENABLED_ENV = "MERGE_GATE_MERGE_ENABLED"


def merge_enabled() -> bool:
    """True iff the operator explicitly enabled real merges (default: observe-only)."""
    return os.environ.get(MERGE_ENABLED_ENV, "false").lower() == "true"


@dataclass
class RuntimeSkills:
    """The configured skill objects a tick runs, plus the toggles."""

    validators: list           # configured builtins (minus per-PR owner_approval) + user
    classifier_rules: list     # configured builtins + user
    static_branch_hooks: list  # break-glass (governance-bound) + user hooks
    disabled: frozenset
    severity_overrides: dict
    enabled: frozenset = frozenset()   # non-empty = allowlist of skills to run
    bot_user: str | None = None        # the bot App's user login (<slug>[bot])


@dataclass(frozen=True)
class PRDecision:
    """What the tick did with one PR (for metrics + logging)."""

    full_name: str
    number: int
    action: str        # "merged" | "observe" | "inbox" | "blocked" | "race-rebased" | "skipped"
    quadrant: str
    detail: str = ""


# -- builders -----------------------------------------------------------------


def _main_head_lookup(api: GitHubAPI):
    def lookup(full_name: str) -> str:
        owner, _, repo = full_name.partition("/")
        return api.main_head_sha(owner, repo)
    return lookup


def _resolve_bot_user(config: AppConfig, api: GitHubAPI) -> str:
    """The bot App's user login (``<slug>[bot]``) — authoritative only.

    The approval/identity checks (C3 receipts, label provenance, diagnostic
    dedupe, R3) trust this login, so it must come from ``GET /app``, never
    from the operator-typed ``env.yml`` ``bot_app_slug``: a transient API
    hiccup combined with a stale config value must not silently change which
    identity the gate trusts. If the authoritative slug is unavailable this
    **fails closed** (raises; the tick aborts rather than guessing). A
    mismatch between the authoritative slug and the config value is surfaced
    loudly but the authoritative one wins.

    Test doubles without App auth (no ``.auth`` attribute, e.g. the FakeAPI)
    have no authoritative source at all and use the config value directly.
    """
    auth = getattr(api, "auth", None)
    if auth is None:
        return f"{config.env.bot_app_slug}[bot]"
    app_slug = auth.app_slug()
    if not app_slug:
        raise RuntimeError(
            "bot identity unavailable: GET /app did not yield the App slug. "
            "Refusing to fall back to config env.bot_app_slug — the "
            "approval/identity path fails closed until the authoritative "
            "slug can be resolved."
        )
    if config.env.bot_app_slug and app_slug != config.env.bot_app_slug:
        logger.warning(
            "config env.bot_app_slug=%r does not match the App's actual slug "
            "%r — using the authoritative slug; fix env.yml.",
            config.env.bot_app_slug, app_slug,
        )
    return f"{app_slug}[bot]"


def _adr_finder(api: GitHubAPI, gov_owner: str, gov_repo: str):
    """Return ``finder(commit_sha) -> bool``: an ADR under ``docs/decisions/``
    in the governance repo references the SHA (L5 break-glass audit)."""
    def finder(commit_sha: str) -> bool:
        try:
            entries = api.list_dir(gov_owner, gov_repo, "docs/decisions")
        except Exception:
            return False
        for e in entries:
            if e.get("type") != "file" or not str(e.get("path", "")).endswith(".md"):
                continue
            try:
                text = api.get_file_text(gov_owner, gov_repo, e["path"])
            except Exception:
                continue
            if text and (commit_sha in text or commit_sha[:7] in text):
                return True
        return False
    return finder


def _apply_severity(validators: list, overrides: dict) -> None:
    for v in validators:
        name = getattr(v, "name", None)
        if name not in overrides or not hasattr(v, "severity"):
            continue
        target = overrides[name]
        if name in CORE_L1_VALIDATORS and target not in _BLOCKING_SEVERITIES:
            logger.warning(
                "ignoring severity_override %s=%s — a core L1 validator may not "
                "be downgraded below blocking (P0/P1).", name, target,
            )
            continue
        v.severity = target


def _enabled(name: str, disabled: frozenset, enabled: frozenset = frozenset()) -> bool:
    if name in NON_DISABLEABLE:
        return True                       # core security skills always run
    if name in disabled:
        return False
    if enabled and name not in enabled:
        return False                      # non-empty `enabled` = allowlist
    return True


def build_runtime_skills(
    config: AppConfig,
    api: GitHubAPI,
    *,
    config_dir: Path | None = None,
    clock=None,
) -> RuntimeSkills:
    """Construct configured built-in skills + user skills; apply toggles."""
    disabled = frozenset(config.skills.disabled)
    enabled = frozenset(config.skills.enabled)
    overrides = dict(config.skills.severity_overrides)
    gov_owner, _, gov_repo = config.projects.governance_repo.partition("/")
    bot_user = _resolve_bot_user(config, api)

    # owner_approval is constructed per-PR (it needs the PR's classifier
    # verdict) in process_pr — it is intentionally absent here.
    builtin_validators = [
        ReadyToMergeValidator(allowlisted_actors=config.owner.allowlisted_actors),
        # R1: seed with the GLOBAL default required_checks. process_pr resolves
        # the per-repo effective value (override > global > ()) just before it
        # evaluates each PR, since one runtime serves every supervised repo.
        CiGreenValidator(
            required_checks=config.env.required_checks,
            allow_no_checks=config.env.allow_no_ci,
            # Publisher trust for required checks: only runs published by the
            # repo's own CI App count as green. Seeded with the GLOBAL
            # ``env.expected_check_publisher`` (default ``github-actions``);
            # process_pr patches the per-repo effective value before each PR,
            # mirroring required_checks.
            expected_check_publisher=(
                config.env.expected_check_publisher or DEFAULT_CHECK_PUBLISHER
            ),
        ),
        TrailersValidator(),
        ClassifierPublisherValidator(
            publisher_slug=config.env.classifier_publisher_slug
        ),
        BaseUpToDateValidator(main_head_sha_lookup=_main_head_lookup(api)),
        AgentRegistryValidator(registry=config.agent_registry),
    ]

    builtin_rules = [
        PathDefaultClassifier(),
        BotSelfRepoClassifier(bot_repo_full_name=config.projects.effective_bot_repo),
        EmptyPrClassifier(),
        AutoRevertClassifier(
            allowlisted_actors=config.owner.allowlisted_actors, bot_user=bot_user
        ),
        # R2: vote the published classifier-judgment quadrant (canonical
        # publisher only). Max-vote means this can only RAISE, never lower.
        PublishedVerdictClassifier(
            publisher_slug=config.env.classifier_publisher_slug
        ),
    ]

    # Repo-agnostic hooks (the ADR finder is governance-bound, not per-repo).
    builtin_hooks = [
        BreakGlassAuditHook(
            allowlisted_actors=config.owner.allowlisted_actors,
            adr_finder=_adr_finder(api, gov_owner, gov_repo),
            adr_deadline_hours=config.projects.break_glass.adr_deadline_hours,
            clock=clock,
            # The bot's own squash of a break-glass-TITLED PR (committer = bot)
            # is not a human break-glass push — wire bot_user so the hook can
            # short-circuit it, exactly as hook_unauthorized_push does below.
            bot_user=bot_user,
        ),
        # R3: code-level branch protection — flag non-bot, non-break-glass,
        # non-allowlisted writes to main.
        UnauthorizedPushHook(
            bot_user=bot_user,
            allowlisted_actors=config.owner.allowlisted_actors,
        ),
    ]

    user = load_user_skills(config_dir / "skills" if config_dir else None)

    # Built-ins always run (modulo `disabled`). A non-empty `enabled` is an
    # allowlist for *user-added* skills only — it can never switch off a
    # built-in gate (that would let everything auto-merge).
    validators = [v for v in builtin_validators if _enabled(v.name, disabled)]
    validators += [v for v in user.validators if _enabled(v.name, disabled, enabled)]
    rules = [r for r in builtin_rules if _enabled(r.name, disabled)]
    rules += [r for r in user.classifier_rules if _enabled(r.name, disabled, enabled)]
    hooks = [h for h in builtin_hooks if _enabled(h.name, disabled)]
    hooks += [h for h in user.branch_hooks if _enabled(h.name, disabled, enabled)]

    _apply_severity(validators, overrides)

    return RuntimeSkills(
        validators=validators,
        classifier_rules=rules,
        static_branch_hooks=hooks,
        disabled=disabled,
        severity_overrides=overrides,
        enabled=enabled,
        bot_user=bot_user,
    )


def build_branch_hooks(runtime: RuntimeSkills, api: GitHubAPI, owner: str, repo: str):
    """Per-repo hook list: static hooks + a repo-bound hallucination guard."""
    hooks = list(runtime.static_branch_hooks)
    if _enabled("hook_hallucination_guard", runtime.disabled):
        def resolver(path: str, sha: str) -> bool:
            return api.file_exists_at_sha(owner, repo, path, sha)
        hooks.append(HallucinationGuardHook(repo_path_resolver=resolver))
    return hooks


# -- per-PR processing --------------------------------------------------------


def _d_reasoning(verdict) -> str:
    reasons = [r for (_n, q, r) in verdict.votes if q == "D"]
    return "; ".join(reasons) or "Quadrant D (irreversible + critical)"


def _quadrant_reasoning(verdict, quadrant: str) -> str:
    reasons = [r for (_n, q, r) in verdict.votes if q == quadrant]
    return "; ".join(reasons) or f"Quadrant {quadrant}"


def _agent_trailer_block(ctx) -> str | None:
    """The PR commits' identity trailers as one deduplicated trailer block.

    A squash merge produces a single bot-authored commit, dropping the PR
    commits' identity trailers. Passing this block as the squash
    ``commit_message`` keeps the audit trail on ``main``. The full L4
    identity set is preserved — every ``Agent-*`` trailer AND ``Task-Ref``
    (the task linkage is part of the audit trail, not an optional extra).
    Distinct values are all kept (e.g. two agents on one PR → two
    ``Agent-Session`` lines), in first-seen order. Returns None when no such
    trailers exist (GitHub then composes its default message).
    """
    lines: list[str] = []
    for commit in ctx.commits:
        for key, value in commit.trailers.raw.items():
            if not (key.startswith("Agent-") or key == "Task-Ref"):
                continue
            line = f"{key}: {value}"
            if line not in lines:
                lines.append(line)
    return "\n".join(lines) or None


def _inbox_issue_exists(api, gov_owner, gov_repo, full_name, number) -> bool:
    for issue in api.list_issues(gov_owner, gov_repo, labels=PENDING_LABEL, state="open"):
        if "pull_request" in issue:
            continue
        if parse_pr_ref(issue.get("body") or "") == (full_name, number):
            return True
    return False


def _post_diagnostic_if_changed(api, ctx, body: str, bot_user: str | None) -> bool:
    """Post the L1 diagnostic, unless the bot's last diagnostic is identical.

    Keeps the bot from re-commenting the same blocked-reasons on every 5-min
    tick (the chattiness the stateless design otherwise invites).

    Only comments **authored by the bot's own identity** count as "the last
    diagnostic": anyone can post a comment starting with the diagnostic
    prefix, and an author-blind match would let a third party suppress the
    bot's real diagnostics. No ``bot_user`` → nothing can be attributed to
    the bot → always post."""
    try:
        comments = api.list_issue_comments(ctx.repo_owner, ctx.repo_name, ctx.number)
    except Exception:
        comments = []
    last = None
    if bot_user:
        for c in comments:
            if ((c.get("user") or {}).get("login")) != bot_user:
                continue
            cb = c.get("body") or ""
            if cb.startswith(DIAGNOSTIC_PREFIX):
                last = cb
    if last == body:
        return False
    api.post_comment(ctx.repo_owner, ctx.repo_name, ctx.number, body)
    return True


def process_pr(api, config, runtime: RuntimeSkills, pr_payload, *, audit_log_path=None) -> PRDecision:
    """Evaluate one open PR and take the resulting action.

    Returns a :class:`PRDecision` describing what happened (no exception on a
    blocked/inbox PR — those are normal outcomes).
    """
    ctx = build_pr_context(api, pr_payload)
    full = ctx.full_name
    # Decision-Inbox issues (pending-owner + B/C audit) go to the configured
    # inbox repo (decision_inbox.repository), defaulting to the governance repo.
    gov_owner, _, gov_repo = config.projects.effective_inbox_repository.partition("/")

    # SHA-bound approvals: the bot's own receipt comments bind each recorded
    # decision label to the exact head SHA it was granted against. Fetched
    # before classify so the auto-revert rule sees them too. A fetch failure
    # SKIPS this PR for the tick (fail closed): proceeding with "no receipts"
    # would let receipt-eligible labels fall through to weaker paths — a label
    # must never be honoured while its receipt cannot be read. The stateless
    # tick simply retries on the next run.
    try:
        pr_comments = api.list_issue_comments(ctx.repo_owner, ctx.repo_name, ctx.number)
    except Exception as e:
        logger.warning(
            "PR %s#%s: comment fetch failed (%s) — approval receipts "
            "unavailable; skipping this PR until they can be read.",
            full, ctx.number, e,
        )
        return PRDecision(
            full, ctx.number, "skipped", "?", "approval receipts unavailable"
        )
    # A3: receipts are verified against THIS PR's repo/number (the keyed-MAC
    # gate in label_provenance) so a leaked App token cannot forge a counting
    # receipt — and a valid receipt from one PR cannot be replayed onto another.
    approved_shas = approval_receipts(
        pr_comments, runtime.bot_user,
        repo_full_name=full, pr_number=ctx.number,
    )
    receipt_times = approval_receipt_times(
        pr_comments, runtime.bot_user,
        repo_full_name=full, pr_number=ctx.number,
    )
    for r in runtime.classifier_rules:
        if r.name == "classifier_auto_revert":
            r.approved_shas = approved_shas

    verdict = classify(ctx, runtime.classifier_rules, audit_log_path)

    # Receipt writer: receipt-eligible labels (C1 ready-to-merge, C3
    # decision:approved-*) are honoured ONLY through a bot SHA receipt
    # (label_provenance), so an allowlisted HAND-applied label is converted
    # into a receipt binding it to the head observed THIS tick. A label
    # applied by a non-allowlisted actor never gets a receipt. The receipts
    # written here are then merged into ``approved_shas`` and honoured the
    # SAME tick: the head cannot change within a tick's execution, so binding
    # to and then validating against the just-observed head is atomic and
    # safe (there is no one-tick deferral). A force-push BETWEEN ticks is
    # caught because the receipt's SHA then differs from the new head, and the
    # writer refuses to re-bind an approval (only the Decision Inbox may
    # supersede an approval receipt). approvals and ready-to-merge behave
    # identically: receipt-required, honoured same-tick once bound.
    to_record = labels_needing_receipt(
        ctx, config.owner.allowlisted_actors, runtime.bot_user,
        approved_shas=approved_shas, receipt_times=receipt_times,
    )
    if to_record:
        approved_shas = dict(approved_shas)
        for label in to_record:
            api.post_comment(
                ctx.repo_owner, ctx.repo_name, ctx.number,
                approval_receipt_comment(
                    label, ctx.head_sha,
                    repo_full_name=full, pr_number=ctx.number,
                ),
            )
            approved_shas[label] = ctx.head_sha

    # R1: resolve this repo's effective required_checks (per-repo override >
    # global env default > ()) and expected check publisher (per-repo override
    # > env default > "github-actions"), and apply both to the CiGreen
    # validator before L1. The runtime's CiGreen carries the global defaults;
    # only the per-repo overrides differ, so we patch the one instance for
    # this PR's repo.
    eff_required = config.projects.effective_required_checks(
        full, config.env.required_checks
    )
    eff_publisher = (
        config.projects.effective_expected_check_publisher(
            full, config.env.expected_check_publisher
        )
        or DEFAULT_CHECK_PUBLISHER
    )
    for v in runtime.validators:
        if v.name == "validator_ci_green":
            v.required_checks = eff_required
            v.expected_check_publisher = eff_publisher
        elif v.name == "validator_ready_to_merge":
            # C1 is receipt-required (mirrors C3): the ready label opens C1
            # only with a bot receipt bound to the current head. This map
            # includes any receipt written this tick (honoured same-tick); a
            # moved head voids a stale receipt.
            v.approved_shas = approved_shas

    # Evaluate the L1 conditions *other than* C3 (owner approval). C3 is the
    # owner gate and is handled separately below: a Quadrant-D PR fails C3 by
    # design and is routed to the Decision Inbox, not reported as "blocked".
    outcome = evaluate_pr(ctx, runtime.validators, classifier_quadrant=verdict.quadrant)
    if not outcome.all_passed:
        _post_diagnostic_if_changed(
            api, ctx, outcome.diagnostic_comment(), runtime.bot_user
        )
        return PRDecision(
            full, ctx.number, "blocked", verdict.quadrant,
            "; ".join(outcome.failure_reasons)[:200],
        )

    # C3 — owner approval. Passes for Quadrant A/B/C (auto-approval), or for a
    # Quadrant-D PR whose ``decision:approved-*`` label is owner/bot-applied
    # and bound to the current head via the bot's SHA receipt (no time
    # fallback; see OwnerApprovalValidator). ``approved_shas`` includes any
    # receipt written this tick. ``runtime.bot_user`` is the App's resolved
    # ``<slug>[bot]`` identity.
    owner_approval = OwnerApprovalValidator(
        classifier_verdict=verdict.quadrant,
        allowlisted_actors=config.owner.allowlisted_actors,
        bot_user=runtime.bot_user,
        approved_shas=approved_shas,
    )
    _apply_severity([owner_approval], runtime.severity_overrides)
    if not owner_approval.check(ctx).passed:
        # Quadrant D with no owner approval yet → route to the Decision Inbox.
        if not _inbox_issue_exists(api, gov_owner, gov_repo, full, ctx.number):
            open_inbox_issue(
                api, gov_owner, gov_repo, full, ctx.number,
                ctx.head_sha, _d_reasoning(verdict),
            )
        return PRDecision(full, ctx.number, "inbox", verdict.quadrant)

    # C3 satisfied → merge, guarded by the L3 race check.
    if not run_l3_race_guard(api, ctx):
        # main advanced past the PR base — rebase the branch, retry next tick.
        api.update_branch(ctx.repo_owner, ctx.repo_name, ctx.number)
        return PRDecision(full, ctx.number, "race-rebased", verdict.quadrant)

    # Observe-only kill-switch: every gate above ran for real, but the merge
    # is allowed only when MERGE_GATE_MERGE_ENABLED=true. In observe mode the
    # would-be merge is recorded instead, and the post-merge audit issue is
    # skipped with it (nothing merged, nothing to audit).
    if not merge_enabled():
        return PRDecision(
            full, ctx.number, "observe", verdict.quadrant,
            f"observe-only: would have merged as Quadrant {verdict.quadrant} "
            f"(set {MERGE_ENABLED_ENV}=true to enable merging)",
        )

    # Squash merging collapses the PR into one bot-authored commit; pass the
    # PR's Agent-* trailers as the commit body so the agent-identity audit
    # trail survives onto main.
    api.merge_pr(
        ctx.repo_owner, ctx.repo_name, ctx.number, head_sha=ctx.head_sha,
        commit_message=_agent_trailer_block(ctx),
    )

    # Passive audit issue for auto-approved B / C — opened only after a
    # successful merge, so a retried tick never double-opens it.
    if verdict.quadrant in AUDIT_LABEL:
        api.open_issue(
            owner=gov_owner, repo=gov_repo,
            title=f"Auto-approved ({verdict.quadrant}) — {full}#{ctx.number}",
            body=(
                f"PR `{full}#{ctx.number}` (head `{ctx.head_sha[:7]}`) was "
                f"auto-merged as Quadrant {verdict.quadrant}.\n\n"
                f"Reasoning: {_quadrant_reasoning(verdict, verdict.quadrant)}\n\n"
                f"This is a passive audit trail — no action needed."
            ),
            labels=[AUDIT_LABEL[verdict.quadrant]],
        )
    return PRDecision(full, ctx.number, "merged", verdict.quadrant)
