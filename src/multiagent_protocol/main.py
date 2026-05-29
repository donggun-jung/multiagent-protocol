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

Incidents are opened idempotently (an open issue referencing the same commit
is not re-opened), so the tick is safe to run every 5 minutes even though the
bot is stateless across ticks.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from multiagent_protocol.auth import AppAuth
from multiagent_protocol.branch_supervisor import (
    load_watermarks,
    revalidate_main,
    save_watermarks,
    scan_repo,
)
from multiagent_protocol.config.loader import load_config
from multiagent_protocol.decision_inbox import resolve_open_issues
from multiagent_protocol.drift_check import (
    check_repo_against_canonical,
    incidents_to_issue_body,
    load_mirror_config,
)
from multiagent_protocol.github_api import GitHubAPI
from multiagent_protocol.runtime import (
    build_branch_hooks,
    build_runtime_skills,
    process_pr,
)

logger = logging.getLogger(__name__)

AUDIT_LOG = Path("bot-state/classifier_audit.jsonl")
MIRROR_PATHS = Path("schemas/mirror_paths.json")


def _has_secrets(env) -> bool:
    return bool(env.get("MERGE_GATE_APP_ID") and env.get("MERGE_GATE_PRIVATE_KEY"))


def _open_incident_if_new(
    api: GitHubAPI, gov_owner: str, gov_repo: str,
    label: str, body: str, dedupe_key: str,
) -> bool:
    """Open an incident issue unless an open one already references dedupe_key."""
    try:
        existing = api.list_issues(gov_owner, gov_repo, labels=label, state="open")
    except Exception as e:
        logger.error("could not list issues for dedupe (%s): %s", label, e)
        existing = []
    for issue in existing:
        if dedupe_key in (issue.get("body") or ""):
            return False
    api.open_issue(
        owner=gov_owner, repo=gov_repo,
        title=f"[{label}] {dedupe_key}", body=body, labels=[label],
    )
    return True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config_dir = Path("config")
    schemas_dir = Path("schemas")
    has_config = config_dir.exists()
    has_secrets = _has_secrets(os.environ)

    # Graceful no-op when run without App credentials. The PUBLIC framework
    # repo has neither secrets nor a config/ (config/ is git-ignored) — it is
    # not a deployment, so the scheduled tick exits cleanly instead of failing
    # every 5 minutes. A deployment that has config/ but no secrets is likely
    # misconfigured: we warn but still exit 0 (do not spam build failures).
    if not has_secrets:
        if has_config:
            logger.warning(
                "config/ is present but MERGE_GATE_APP_ID / MERGE_GATE_PRIVATE_KEY "
                "are not set. If this is your deployment, add the Actions secrets "
                "(see docs/guide/quick-start.md). Skipping this tick."
            )
        else:
            logger.info(
                "no App credentials and no config/ — this is the framework "
                "upstream, not a deployment. Nothing to do; exiting cleanly."
            )
        return 0

    if not has_config:
        logger.error("config/ directory not found. Run the wizard first.")
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
    watermarks = load_watermarks()
    supervised = list(config.projects.supervised_repos)
    metrics: dict[str, int] = {
        "merged": 0, "inbox": 0, "blocked": 0, "race-rebased": 0,
        "l5_incidents": 0, "l2_incidents": 0, "drift_incidents": 0,
        "inbox_resolved": 0,
    }

    for inst in installations:
        account = (inst.get("account") or {}).get("login")
        api = GitHubAPI(auth, inst["id"])
        runtime = build_runtime_skills(config, api, config_dir=config_dir)

        for full in [r for r in supervised if r.split("/")[0] == account]:
            owner, _, name = full.partition("/")

            # L1 + L3 + L4 per open PR.
            try:
                prs = api.list_open_prs(owner, name)
            except Exception as e:
                logger.error("list PRs failed for %s: %s", full, e)
                prs = []
            for pr_payload in prs:
                number = pr_payload.get("number")
                try:
                    d = process_pr(api, config, runtime, pr_payload, audit_log_path=AUDIT_LOG)
                    metrics[d.action] = metrics.get(d.action, 0) + 1
                    logger.info(
                        "PR %s#%s → %s (Q%s) %s",
                        full, number, d.action, d.quadrant, d.detail,
                    )
                except Exception as e:
                    logger.error("process PR %s#%s failed: %s", full, number, e)

            # L5 break-glass + hallucination scan.
            try:
                incidents, wm = scan_repo(
                    api, owner, name, build_branch_hooks(runtime, api, owner, name), watermarks
                )
                for inc in incidents:
                    if _open_incident_if_new(
                        api, gov_owner, gov_repo, inc.label, inc.body, inc.commit_sha[:7]
                    ):
                        metrics["l5_incidents"] += 1
                if wm:
                    watermarks[f"{owner}/{name}"] = wm
            except Exception as e:
                logger.error("L5 scan failed for %s: %s", full, e)

            # L2 post-merge re-validation.
            try:
                l2_incidents, l2_wm = revalidate_main(api, owner, name, (), watermarks)
                for inc in l2_incidents:
                    if _open_incident_if_new(
                        api, gov_owner, gov_repo, inc.label, inc.body, inc.commit_sha[:7]
                    ):
                        metrics["l2_incidents"] += 1
                if l2_wm:
                    watermarks[f"{owner}/{name}:l2"] = l2_wm
            except Exception as e:
                logger.error("L2 re-validation failed for %s: %s", full, e)

        # Governance-scoped work runs under the installation that owns it.
        if account == gov_owner:
            if MIRROR_PATHS.exists():
                try:
                    mirror = load_mirror_config(MIRROR_PATHS)
                    drift = []
                    for full in [r for r in supervised if r.split("/")[0] == account]:
                        a_owner, _, a_repo = full.partition("/")
                        drift += check_repo_against_canonical(
                            api, gov_owner, gov_repo, a_owner, a_repo, mirror
                        )
                    if drift and _open_incident_if_new(
                        api, gov_owner, gov_repo,
                        "decision:mirror-drift-incident",
                        incidents_to_issue_body(drift), "mirror-drift",
                    ):
                        metrics["drift_incidents"] += 1
                except Exception as e:
                    logger.error("drift check failed: %s", e)

            try:
                resolutions = resolve_open_issues(
                    api, gov_owner, gov_repo, config.owner.allowlisted_actors
                )
                metrics["inbox_resolved"] += len(resolutions)
                for r in resolutions:
                    logger.info("inbox: %s#%s → %s (%s)",
                                r.pr_full_name, r.pr_number, r.verdict, r.action)
            except Exception as e:
                logger.error("inbox poll failed: %s", e)

    try:
        save_watermarks(watermarks)
    except Exception as e:
        logger.error("could not persist watermarks: %s", e)

    logger.info("tick complete: %s", metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
