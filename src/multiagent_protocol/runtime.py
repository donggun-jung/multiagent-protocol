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
from multiagent_protocol.skills.builtin.hook_break_glass_audit import (
    BreakGlassAuditHook,
)
from multiagent_protocol.skills.builtin.hook_hallucination_guard import (
    HallucinationGuardHook,
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
})

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
        if name in overrides and hasattr(v, "severity"):
            v.severity = overrides[name]


def _enabled(name: str, disabled: frozenset) -> bool:
    return name not in disabled or name in NON_DISABLEABLE


def build_runtime_skills(
    config: AppConfig,
    api: GitHubAPI,
    *,
    config_dir: Path | None = None,
    clock=None,
) -> RuntimeSkills:
    """Construct configured built-in skills + user skills; apply toggles."""
    disabled = frozenset(config.skills.disabled)
    overrides = dict(config.skills.severity_overrides)
    gov_owner, _, gov_repo = config.projects.governance_repo.partition("/")

    # owner_approval is constructed per-PR (it needs the PR's classifier
    # verdict) in process_pr — it is intentionally absent here.
    builtin_validators = [
        ReadyToMergeValidator(allowlisted_actors=config.owner.allowlisted_actors),
        CiGreenValidator(),
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
        AutoRevertClassifier(),
    ]

    # Repo-agnostic hooks (the ADR finder is governance-bound, not per-repo).
    builtin_hooks = [
        BreakGlassAuditHook(
            allowlisted_actors=config.owner.allowlisted_actors,
            adr_finder=_adr_finder(api, gov_owner, gov_repo),
            adr_deadline_hours=config.projects.break_glass.adr_deadline_hours,
            clock=clock,
        ),
    ]

    user = load_user_skills(config_dir / "skills" if config_dir else None)

    validators = [v for v in builtin_validators if _enabled(v.name, disabled)]
    validators += list(user.validators)
    rules = [r for r in builtin_rules if _enabled(r.name, disabled)]
    rules += list(user.classifier_rules)
    hooks = [h for h in builtin_hooks if _enabled(h.name, disabled)]
    hooks += list(user.branch_hooks)

    _apply_severity(validators, overrides)

    return RuntimeSkills(
        validators=validators,
        classifier_rules=rules,
        static_branch_hooks=hooks,
        disabled=disabled,
        severity_overrides=overrides,
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
    gov_owner, _, gov_repo = config.projects.governance_repo.partition("/")

    verdict = classify(ctx, runtime.classifier_rules, audit_log_path)

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
    # Prefer the App's *actual* slug (authoritative) over operator-typed config
    # so a mistyped bot_app_slug cannot silently break the approve→merge flow.
    auth = getattr(api, "auth", None)
    app_slug = auth.app_slug() if auth is not None else None
    owner_approval = OwnerApprovalValidator(
        classifier_verdict=verdict.quadrant,
        allowlisted_actors=config.owner.allowlisted_actors,
        bot_user=f"{app_slug or config.env.bot_app_slug}[bot]",
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
