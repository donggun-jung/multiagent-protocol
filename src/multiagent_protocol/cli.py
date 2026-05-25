"""Command-line interface entry point.

Subcommands:

- ``tick``: one cron tick. Equivalent to ``python -m multiagent_protocol``.
- ``init``: interactive bootstrap (calls into the wizard logic in pure Python).
- ``check-config``: validate the config files against schemas without
  running a tick.

Most users will invoke the CLI via the bot-cron workflow, not by hand.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _cmd_tick(_args) -> int:
    from multiagent_protocol.main import main as tick_main
    return tick_main([])


def _cmd_check_config(args) -> int:
    from multiagent_protocol.config.loader import load_config

    config_dir = Path(args.config_dir)
    schemas_dir = Path(args.schemas_dir)
    try:
        cfg = load_config(config_dir, schemas_dir if schemas_dir.exists() else None)
    except Exception as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    print("config OK")
    print(f"  owner: {cfg.owner.github_login}")
    print(f"  allowlisted: {', '.join(cfg.owner.allowlisted_actors)}")
    print(f"  governance: {cfg.projects.governance_repo}")
    print(f"  supervised: {', '.join(cfg.projects.supervised_repos) or '(none)'}")
    print(f"  runner_tier: {cfg.env.runner_tier}")
    return 0


def _cmd_init(_args) -> int:
    print("Interactive init is provided by the web wizard.")
    print("Open docs/wizard/index.html in a browser, fill the form, and")
    print("download the generated config/*.yml files into this repo.")
    print()
    print("If you cannot open a browser, see docs/guide/quick-start.md for")
    print("the manual config-file content.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="multiagent-protocol")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tick", help="run one cron tick")
    sub.add_parser("init", help="bootstrap a new installation")

    p_check = sub.add_parser("check-config", help="validate config files")
    p_check.add_argument("--config-dir", default="config")
    p_check.add_argument("--schemas-dir", default="schemas")

    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level)

    if args.cmd == "tick":
        return _cmd_tick(args)
    if args.cmd == "init":
        return _cmd_init(args)
    if args.cmd == "check-config":
        return _cmd_check_config(args)
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
