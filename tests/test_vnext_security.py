"""Tests for the fix/vnext-security hardening pass.

End-to-end (FakeAPI + runtime) and unit coverage for:

1. Required-check publisher trust (C2 honours only the expected CI App).
2. SHA-bound approvals (receipts written by the bot; stale receipts void).
3. ``governance/`` as a critical path in the default path classifier.
4. Core L1 validators are non-disableable (runtime + schema).
6. Bot identity fails closed when the authoritative App slug is unavailable.
7. Diagnostic-comment dedupe only trusts the bot's own comments.
8. Squash merges preserve the PR's Agent-* identity trailers.
"""

from __future__ import annotations

import dataclasses

from multiagent_protocol.runtime import build_runtime_skills, process_pr
from tests.conftest import changed_file, make_check

BOT_USER = "your-merge-gate-bot[bot]"  # solo_config env.bot_app_slug + "[bot]"


def _rt(api, cfg):
    return build_runtime_skills(cfg, api, config_dir=None)


# -- 1. Required-check publisher trust, wired through the runtime -------------

def test_required_check_green_from_foreign_app_blocks_e2e(fake_api, solo_config):
    # env.required_checks=("ci",); the only 'ci' run is green but published by
    # an attacker-controlled App → C2 treats it as not yet green → blocked.
    cfg = dataclasses.replace(
        solo_config, env=dataclasses.replace(solo_config.env, required_checks=("ci",)))
    pr = fake_api.register_pr(
        number=70, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("ci", "success", slug="attacker-app")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "blocked"
    assert fake_api.merged == []


def test_required_check_green_from_expected_app_merges_e2e(fake_api, solo_config):
    # The same PR with the 'ci' run published by github-actions (the default
    # expected publisher) merges.
    cfg = dataclasses.replace(
        solo_config, env=dataclasses.replace(solo_config.env, required_checks=("ci",)))
    pr = fake_api.register_pr(
        number=71, labels=("ready-to-merge",), files=[changed_file("README.md")],
        checks=[make_check("ci", "success", slug="github-actions")])
    d = process_pr(fake_api, cfg, _rt(fake_api, cfg), pr)
    assert d.action == "merged"
