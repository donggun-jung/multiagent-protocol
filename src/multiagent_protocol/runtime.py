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
"""

from __future__ import annotations

import logging
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
from multiagent_protocol.skills.builtin.validator_ci_green import CiGreenValidator
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
    action: str        # "merged" | "inbox" | "blocked" | "race-rebased" | "skipped"
    quadrant: str
    detail: str = ""


# -- builders -----------------------------------------------------------------


def _main_head_lookup(api: GitHubAPI):
    def lookup(full_name: str) -> str:
        owner, _, repo = full_name.partition("/")
        return api.main_head_sha(owner, repo)
    return lookup


def _resolve_bot_user(config: AppConfig, api: GitHubAPI) -> str:
    """The bot App's user login (``<slug>[bot]``).

    Prefer the App's *actual* slug (authoritative, from ``GET /app``) over the
    operator-typed ``env.yml`` ``bot_app_slug`` so a typo cannot silently break
    the approve→merge flow. Falls back to config if the lookup is unavailable
    (e.g. the FakeAPI in tests has no ``.auth``)."""
    auth = getattr(api, "auth", None)
    app_slug = auth.app_slug() if auth is not None else None
    return f"{app_slug or config.env.bot_app_slug}[bot]"


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


def _inbox_issue_exists(api, gov_owner, gov_repo, full_name, number) -> bool:
    for issue in api.list_issues(gov_owner, gov_repo, labels=PENDING_LABEL, state="open"):
        if "pull_request" in issue:
            continue
        if parse_pr_ref(issue.get("body") or "") == (full_name, number):
            return True
    return False


def _post_diagnostic_if_changed(api, ctx, body: str) -> bool:
    """Post the L1 diagnostic, unless the bot's last diagnostic is identical.

    Keeps the bot from re-commenting the same blocked-reasons on every 5-min
    tick (the chattiness the stateless design otherwise invites)."""
    try:
        comments = api.list_issue_comments(ctx.repo_owner, ctx.repo_name, ctx.number)
    except Exception:
        comments = []
    last = None
    for c in comments:
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

    verdict = classify(ctx, runtime.classifier_rules, audit_log_path)

    # R1: resolve this repo's effective required_checks (per-repo override >
    # global env default > ()) and apply it to the CiGreen validator before L1.
    # The runtime's CiGreen carries the global default; only the per-repo
    # override differs, so we patch the one instance for this PR's repo.
    eff_required = config.projects.effective_required_checks(
        full, config.env.required_checks
    )
    for v in runtime.validators:
        if v.name == "validator_ci_green":
            v.required_checks = eff_required

    # Evaluate the L1 conditions *other than* C3 (owner approval). C3 is the
    # owner gate and is handled separately below: a Quadrant-D PR fails C3 by
    # design and is routed to the Decision Inbox, not reported as "blocked".
    outcome = evaluate_pr(ctx, runtime.validators, classifier_quadrant=verdict.quadrant)
    if not outcome.all_passed:
        _post_diagnostic_if_changed(api, ctx, outcome.diagnostic_comment())
        return PRDecision(
            full, ctx.number, "blocked", verdict.quadrant,
            "; ".join(outcome.failure_reasons)[:200],
        )

    # C3 — owner approval. Passes for Quadrant A/B/C (auto-approval), or for a
    # Quadrant-D PR whose ``decision:approved-*`` label was applied by the
    # owner/bot at or after the current head (see OwnerApprovalValidator).
    # ``runtime.bot_user`` is the App's resolved ``<slug>[bot]`` identity.
    owner_approval = OwnerApprovalValidator(
        classifier_verdict=verdict.quadrant,
        allowlisted_actors=config.owner.allowlisted_actors,
        bot_user=runtime.bot_user,
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

    api.merge_pr(ctx.repo_owner, ctx.repo_name, ctx.number, head_sha=ctx.head_sha)

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
