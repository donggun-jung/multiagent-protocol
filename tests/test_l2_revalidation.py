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


def test_l2_required_in_progress_is_unsettled_not_passed(fake_api):
    # Cross-vendor re-review residual: a REQUIRED check still running
    # (queued/in_progress) on a merged commit must NOT pass + advance the
    # watermark — a required check that later fails would otherwise be missed
    # (it landed before CI finished). It is unsettled → retry next tick.
    _seed(fake_api, "f" * 40, [make_check("build", "", status="in_progress")])
    incidents, wm = revalidate_main(fake_api, "o", "r", ("build",), {})
    assert incidents == []
    assert wm is None  # watermark does NOT advance past the unsettled commit


def test_no_new_commits_returns_watermark(fake_api):
    fake_api.seed_main_commits("o", "r", [])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {"o/r:l2": "x" * 40})
    assert incidents == []
    assert wm == "x" * 40


def test_no_checks_unsettled_by_default(fake_api):
    # A commit with no check-runs is left unsettled (fail-closed, like C2) so a
    # regression that landed before CI is not silently passed.
    _seed(fake_api, "f" * 40, [])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {})
    assert incidents == []
    assert wm is None   # watermark did NOT advance


def test_no_checks_passes_with_allow_no_ci(fake_api):
    _seed(fake_api, "g" * 40, [])
    incidents, wm = revalidate_main(fake_api, "o", "r", (), {}, allow_no_ci=True)
    assert incidents == []
    assert wm == "g" * 40   # opted-in → settled


# -- R1: L2 honours required_checks --------------------------------------------

def test_l2_required_check_failure_opens_incident(fake_api):
    # The named required check 'build' is present + failing → real failure.
    _seed(fake_api, "h" * 40, [make_check("build", "failure")])
    incidents, wm = revalidate_main(fake_api, "o", "r", ("build",), {})
    assert len(incidents) == 1
    assert incidents[0].commit_sha == "h" * 40
    assert wm == "h" * 40


def test_l2_required_check_only_inspects_named(fake_api):
    # 'build' (required) is green; an unrelated 'flaky' check is red. Because L2
    # filters to required checks, the unrelated red does not open an incident.
    _seed(fake_api, "i" * 40,
          [make_check("build", "success"), make_check("flaky", "failure")])
    incidents, wm = revalidate_main(fake_api, "o", "r", ("build",), {})
    assert incidents == []
    assert wm == "i" * 40


def test_l2_required_check_missing_is_real_failure(fake_api):
    # R1-in-L2 fix: the required check 'build' is absent on the merged commit
    # (only 'lint' ran). Mirroring C2 (which FAILS on a missing required check,
    # not "wait"), a specified-and-missing required check is a REAL failure →
    # incident opens and the watermark advances past the settled commit.
    # (Previously this was silently classed infra/unsettled — a latent
    # fail-open where a never-appearing required check never alarmed.)
    _seed(fake_api, "j" * 40, [make_check("lint", "success")])
    incidents, wm = revalidate_main(fake_api, "o", "r", ("build",), {})
    assert len(incidents) == 1
    assert incidents[0].commit_sha == "j" * 40
    assert "build" in incidents[0].body
    assert wm == "j" * 40   # settled (incident raised)


def test_l2_required_missing_is_incident_even_with_allow_no_ci(fake_api):
    # R1-in-L2 fail-closed fix (the cited bypass): a specified-and-missing
    # required check must open an incident REGARDLESS of allow_no_ci. allow_no_ci
    # only relaxes the EMPTY-required-list path; it must not silently 'pass' a
    # missing named required check (which previously returned 'passed' here).
    _seed(fake_api, "k" * 40, [make_check("lint", "success")])
    incidents, wm = revalidate_main(
        fake_api, "o", "r", ("build",), {}, allow_no_ci=True)
    assert len(incidents) == 1
    assert incidents[0].commit_sha == "k" * 40
    assert "build" in incidents[0].body
    assert wm == "k" * 40


def test_l2_required_duplicate_failure_then_success_is_incident(fake_api):
    # R1-in-L2 + duplicate masking: 'build' appears twice (failure then
    # success). A name-deduped view would mask the failure; L2 inspects all
    # same-named runs, so the failing duplicate opens an incident.
    _seed(fake_api, "l" * 40,
          [make_check("build", "failure"), make_check("build", "success")])
    incidents, wm = revalidate_main(fake_api, "o", "r", ("build",), {})
    assert len(incidents) == 1
    assert incidents[0].commit_sha == "l" * 40
    assert wm == "l" * 40


# -- ITEM 2: L2 publisher trust mirrors C2 ------------------------------------

def test_l2_required_green_from_foreign_app_is_incident(fake_api):
    # ITEM 2: the required check 'build' is green but published by a FOREIGN
    # app. The pre-merge C2 already rejects this; L2 must too — treat it as
    # not-satisfied → a real failure (incident), not a silent pass. Without the
    # publisher gate this commit would have been classed 'passed'.
    _seed(fake_api, "m" * 40, [make_check("build", "success", slug="attacker-app")])
    incidents, wm = revalidate_main(
        fake_api, "o", "r", ("build",), {},
        expected_check_publisher="github-actions")
    assert len(incidents) == 1
    assert incidents[0].label == "decision:post-merge-revalidation"
    assert incidents[0].commit_sha == "m" * 40
    assert "build" in incidents[0].body
    assert wm == "m" * 40   # settled (incident raised)


def test_l2_required_green_from_expected_app_passes(fake_api):
    # Control: the same green required check published by the expected app
    # satisfies L2 (no incident, watermark advances).
    _seed(fake_api, "n" * 40, [make_check("build", "success", slug="github-actions")])
    incidents, wm = revalidate_main(
        fake_api, "o", "r", ("build",), {},
        expected_check_publisher="github-actions")
    assert incidents == []
    assert wm == "n" * 40


def test_l2_required_expected_plus_foreign_green_passes(fake_api):
    # One green run from the expected app satisfies L2; an extra foreign green
    # neither helps nor hurts (mirrors C2's named-required path).
    _seed(fake_api, "o" * 40, [
        make_check("build", "success", slug="github-actions"),
        make_check("build", "success", slug="some-other-app"),
    ])
    incidents, wm = revalidate_main(
        fake_api, "o", "r", ("build",), {},
        expected_check_publisher="github-actions")
    assert incidents == []
    assert wm == "o" * 40


def test_l2_required_publisher_gate_skipped_when_none(fake_api):
    # With the publisher gate disabled (None) a foreign-published green passes —
    # the publisher-agnostic legacy behavior.
    _seed(fake_api, "p" * 40, [make_check("build", "success", slug="attacker-app")])
    incidents, wm = revalidate_main(
        fake_api, "o", "r", ("build",), {}, expected_check_publisher=None)
    assert incidents == []
    assert wm == "p" * 40


def test_l2_required_missing_app_slug_is_incident_when_publisher_expected(fake_api):
    # A required run with no app slug cannot prove its publisher → not-satisfied
    # → real failure (fail closed), mirroring C2's <missing app> handling.
    _seed(fake_api, "q" * 40, [make_check("build", "success", slug=None)])
    incidents, wm = revalidate_main(
        fake_api, "o", "r", ("build",), {},
        expected_check_publisher="github-actions")
    assert len(incidents) == 1
    assert incidents[0].commit_sha == "q" * 40
    assert wm == "q" * 40


def test_l2_default_publisher_is_github_actions(fake_api):
    # The default expected publisher (no kwarg) is 'github-actions': a foreign
    # green required check is an incident even without explicitly passing it.
    # This documents the safe default that ships before the main.py wiring
    # follow-up lands.
    _seed(fake_api, "r" * 40, [make_check("build", "success", slug="attacker-app")])
    incidents, wm = revalidate_main(fake_api, "o", "r", ("build",), {})
    assert len(incidents) == 1
    assert incidents[0].commit_sha == "r" * 40
