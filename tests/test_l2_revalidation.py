"""L2 post-merge re-validation (branch_supervisor.revalidate_main).

Verifies the infra-vs-real failure differentiation from architecture.md § L2:
- real failure (e.g. ``failure``) → incident, watermark advances past it.
- infra failure (``cancelled`` / zero-duration) → no incident, watermark does
  NOT advance (the commit is re-checked next tick).
- ``skipped`` and ``success`` → pass.
"""

from __future__ import annotations

from multiagent_protocol.branch_supervisor import revalidate_main
from tests.conftest import make_check, raw_commit


def _seed(fake_api, sha, checks):
    c = raw_commit(sha=sha)
    fake_api.seed_main_commits("o", "r", [c])
    fake_api._checks[sha] = checks
    return c


def test_real_failure_opens_incident_and_advances(fake_api):
    _seed(fake_api, "a" * 40, [make_check("test", "failure")])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {})
    assert len(incidents) == 1
    assert incidents[0].label == "decision:post-merge-revalidation"
    assert incidents[0].commit_sha == "a" * 40
    assert wm == "a" * 40


def test_infra_cancelled_no_incident_unsettled(fake_api):
    _seed(fake_api, "b" * 40, [make_check("test", "cancelled")])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {})
    assert incidents == []
    assert wm is None  # did not advance past the infra-failing commit


def test_zero_duration_is_infra(fake_api):
    _seed(fake_api, "e" * 40, [make_check(
        "test", "failure",
        started_at="2026-05-25T00:00:00Z", completed_at="2026-05-25T00:00:00Z")])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {})
    assert incidents == []
    assert wm is None


def test_skipped_passes(fake_api):
    _seed(fake_api, "c" * 40, [make_check("optional", "skipped")])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {})
    assert incidents == []
    assert wm == "c" * 40


def test_success_passes(fake_api):
    _seed(fake_api, "d" * 40, [make_check("test", "success")])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {})
    assert incidents == []
    assert wm == "d" * 40


def test_no_new_commits_returns_watermark(fake_api):
    fake_api.seed_main_commits("o", "r", [])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {"o/r:l2": "x" * 40})
    assert incidents == []
    assert wm == "x" * 40
