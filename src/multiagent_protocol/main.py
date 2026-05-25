"""Cron entry point — one tick.

Wires the 4 modules together:

1. Load config + skills.
2. Discover App installations.
3. For each supervised repo:
   - For each open PR: pr_validator → classifier → (merge or comment or open inbox issue).
   - branch_supervisor: scan main HEAD for break-glass commits.
4. drift_check across all canonical paths.
5. Persist watermarks + metrics; exit.

This file is the orchestrator. It does not contain logic that is part of
the bot's enforcement — that lives in the modules.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from multiagent_protocol.auth import AppAuth
from multiagent_protocol.config.loader import load_config
from multiagent_protocol.skills.loader import load_all

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    argv = argv if argv is not None else sys.argv[1:]

    # Load configuration.
    config_dir = Path("config")
    schemas_dir = Path("schemas")
    if not config_dir.exists():
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

    # Load skills.
    skills = load_all(config_root=config_dir / "skills")
    logger.info(
        "skills: %d validators, %d classifier rules, %d branch hooks "
        "(%d user skill load failures)",
        len(skills.validators),
        len(skills.classifier_rules),
        len(skills.branch_hooks),
        len(skills.user_skill_load_failures),
    )

    # Authenticate. The bot runs as a GitHub App; credentials from env.
    try:
        auth = AppAuth.from_env()
    except Exception as e:
        logger.error("auth failed: %s", e)
        return 3

    # Discover installations.
    try:
        installations = auth.installations()
    except Exception as e:
        logger.error("could not list App installations: %s", e)
        return 4

    logger.info("found %d installations", len(installations))

    # The actual per-repo processing loop is intentionally omitted here for
    # the v0.1.0 release: it requires the GitHubAPI client to be wired into
    # pr_validator / branch_supervisor / drift_check, which need integration
    # tests against real GitHub or VCR cassettes. Those land in v0.2.0.
    #
    # The skeleton above is enough to: (1) load config, (2) load skills,
    # (3) authenticate, (4) list installations. Operators running v0.1.0
    # can verify their App installation by looking at this output.
    logger.info("tick complete: %d installations discovered", len(installations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
