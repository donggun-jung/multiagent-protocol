"""Tests for built-in validators."""

from __future__ import annotations

from multiagent_protocol.skills.builtin.validator_base_up_to_date import (
    BaseUpToDateValidator,
)
from multiagent_protocol.skills.builtin.validator_ci_green import CiGreenValidator
from multiagent_protocol.skills.builtin.validator_classifier_publisher import (
    ClassifierPublisherValidator,
)
from multiagent_protocol.skills.builtin.validator_ready_to_merge import (
    ReadyToMergeValidator,
)
from multiagent_protocol.skills.builtin.validator_trailers import TrailersValidator
from multiagent_protocol.types import CheckRunStatus, LabelEvent, TrailerSet

# -- Trailers --

def test_trailers_validator_ok(commit_factory, pr_factory):
    trailers = TrailerSet(
        agent_tool="claude-code",
        agent_model="claude-opus-4.7",
        agent_session="s_test123",
        agent_machine="ci",
        task_ref="PR#1",
    )
    pr = pr_factory(commits=(commit_factory(trailers=trailers),))
    assert TrailersValidator().check(pr).passed


def test_trailers_validator_missing_field(commit_factory, pr_factory):
    trailers = TrailerSet(
        agent_tool="claude-code",
        agent_model="",
        agent_session="s_test123",
        agent_machine="ci",
        task_ref="PR#1",
    )
    pr = pr_factory(commits=(commit_factory(trailers=trailers),))
    r = TrailersValidator().check(pr)
    assert not r.passed
    assert "Agent-Model" in r.failure_reason


def test_trailers_validator_malformed_session(commit_factory, pr_factory):
    trailers = TrailerSet(
        agent_tool="x",
        agent_model="x",
        agent_session="s_ends-with-hyphen-",
        agent_machine="x",
        task_ref="none",
    )
    pr = pr_factory(commits=(commit_factory(trailers=trailers),))
    r = TrailersValidator().check(pr)
    assert not r.passed
    assert "malformed Agent-Session" in r.failure_reason


def test_trailers_validator_malformed_task_ref(commit_factory, pr_factory):
    trailers = TrailerSet(
        agent_tool="x",
        agent_model="x",
        agent_session="s_test123",
        agent_machine="x",
        task_ref="invalid/foo",
    )
    pr = pr_factory(commits=(commit_factory(trailers=trailers),))
    r = TrailersValidator().check(pr)
    assert not r.passed
    assert "Task-Ref" in r.failure_reason


# -- Ready to merge --

def test_ready_to_merge_label_present(pr_factory):
    pr = pr_factory(labels=("ready-to-merge",))
    assert ReadyToMergeValidator().check(pr).passed


def test_ready_to_merge_label_absent(pr_factory):
    pr = pr_factory(labels=())
    r = ReadyToMergeValidator().check(pr)
    assert not r.passed
    assert "ready-to-merge" in r.failure_reason


def test_ready_to_merge_with_allowlist_actor(pr_factory):
    pr = pr_factory(
        labels=("ready-to-merge",),
        label_events=(
            LabelEvent(
                label="ready-to-merge",
                actor_login="owner",
                created_at="2026-05-25T00:00:00Z",
            ),
        ),
    )
    v = ReadyToMergeValidator(allowlisted_actors=("owner",))
    assert v.check(pr).passed


def test_ready_to_merge_label_applied_by_non_allowlisted_actor(pr_factory):
    pr = pr_factory(
        labels=("ready-to-merge",),
        label_events=(
            LabelEvent(
                label="ready-to-merge",
                actor_login="impostor",
                created_at="2026-05-25T00:00:00Z",
            ),
        ),
    )
    v = ReadyToMergeValidator(allowlisted_actors=("owner",))
    r = v.check(pr)
    assert not r.passed
    assert "allowlisted actor" in r.failure_reason


# -- CI green --

def test_ci_green_no_required_list_strict(pr_factory):
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="lint", status="completed", conclusion="success",
            started_at=None, completed_at=None, app_slug=None, output_summary=None,
        ),
        CheckRunStatus(
            name="test", status="completed", conclusion="success",
            started_at=None, completed_at=None, app_slug=None, output_summary=None,
        ),
    ))
    assert CiGreenValidator().check(pr).passed


def test_ci_green_one_failure(pr_factory):
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="lint", status="completed", conclusion="success",
            started_at=None, completed_at=None, app_slug=None, output_summary=None,
        ),
        CheckRunStatus(
            name="test", status="completed", conclusion="failure",
            started_at=None, completed_at=None, app_slug=None, output_summary=None,
        ),
    ))
    r = CiGreenValidator().check(pr)
    assert not r.passed
    assert "test" in r.failure_reason


def test_ci_green_required_list_missing_check(pr_factory):
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="lint", status="completed", conclusion="success",
            started_at=None, completed_at=None, app_slug=None, output_summary=None,
        ),
    ))
    v = CiGreenValidator(required_checks=("lint", "test"))
    r = v.check(pr)
    assert not r.passed
    assert "'test' is missing" in r.failure_reason


def test_ci_green_required_present_plus_unrelated_passes(pr_factory):
    # R1: each named required check is present+green; an unrelated extra check
    # does not matter (the required-list path only inspects named checks).
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="test", status="completed", conclusion="success",
            started_at=None, completed_at=None, app_slug=None, output_summary=None,
        ),
        CheckRunStatus(
            name="some-other-check", status="completed", conclusion="neutral",
            started_at=None, completed_at=None, app_slug=None, output_summary=None,
        ),
    ))
    assert CiGreenValidator(required_checks=("test",)).check(pr).passed


def test_ci_green_required_missing_fails_even_with_allow_no_checks(pr_factory):
    # R1 fail-closed: allow_no_checks only relaxes the EMPTY-required-list path.
    # When a required check is named, a missing one FAILS regardless.
    pr = pr_factory(check_runs=())
    v = CiGreenValidator(required_checks=("test",), allow_no_checks=True)
    r = v.check(pr)
    assert not r.passed
    assert "'test' is missing" in r.failure_reason


def test_ci_green_neutral_passes_strict(pr_factory):
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="classifier-judgment", status="completed", conclusion="neutral",
            started_at=None, completed_at=None, app_slug="github-actions",
            output_summary="Quadrant: A",
        ),
    ))
    # 'neutral' is informational, treated as pass in default mode.
    assert CiGreenValidator().check(pr).passed


def test_ci_green_empty_check_runs_fails(pr_factory):
    pr = pr_factory(check_runs=())
    r = CiGreenValidator().check(pr)
    assert not r.passed


def test_ci_green_allow_no_checks_passes(pr_factory):
    # Opt-in (env.yml allow_no_ci): a head with zero checks passes vacuously.
    pr = pr_factory(check_runs=())
    assert CiGreenValidator(allow_no_checks=True).check(pr).passed


def _check(name: str, conclusion: str, *, status: str = "completed") -> CheckRunStatus:
    return CheckRunStatus(
        name=name, status=status, conclusion=conclusion,
        started_at=None, completed_at=None, app_slug=None, output_summary=None,
    )


# -- R1 duplicate check-name masking (fail-open fix) --------------------------

def test_ci_green_required_duplicate_failure_then_success_fails(pr_factory):
    # R1 fix: two check-runs both named 'build' — one failure, one (later)
    # success. A name-deduped {name: check} map would collapse to the success
    # and PASS C2 (fail-OPEN). A required check is green only if NO same-name run
    # is non-success, so the failing duplicate must FAIL C2 fail-closed.
    pr = pr_factory(check_runs=(
        _check("build", "failure"),
        _check("build", "success"),
    ))
    r = CiGreenValidator(required_checks=("build",)).check(pr)
    assert not r.passed
    assert "build" in r.failure_reason


def test_ci_green_required_single_success_passes(pr_factory):
    # The non-duplicate baseline: a single green required 'build' still passes.
    pr = pr_factory(check_runs=(_check("build", "success"),))
    assert CiGreenValidator(required_checks=("build",)).check(pr).passed


def test_ci_green_no_required_checks_unchanged_by_fix(pr_factory):
    # Backward-compat: with no required_checks list, the strict all-checks path
    # is unchanged — all-success passes, any failure (incl. a duplicate) fails.
    all_green = pr_factory(check_runs=(
        _check("lint", "success"), _check("test", "success"),
    ))
    assert CiGreenValidator().check(all_green).passed

    dup_fail = pr_factory(check_runs=(
        _check("build", "success"), _check("build", "failure"),
    ))
    r = CiGreenValidator().check(dup_fail)
    assert not r.passed
    assert "build" in r.failure_reason


# -- Base up-to-date --

def test_base_up_to_date_matches(pr_factory):
    pr = pr_factory(base_sha="abc1234567890" + "0" * 27)
    v = BaseUpToDateValidator(
        main_head_sha_lookup=lambda full_name: "abc1234567890" + "0" * 27
    )
    assert v.check(pr).passed


def test_base_up_to_date_mismatch(pr_factory):
    pr = pr_factory(base_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    v = BaseUpToDateValidator(
        main_head_sha_lookup=lambda f: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    r = v.check(pr)
    assert not r.passed
    assert "stale" in r.failure_reason


def test_base_up_to_date_no_lookup_passes(pr_factory):
    pr = pr_factory()
    assert BaseUpToDateValidator().check(pr).passed


# -- Classifier publisher --

def test_classifier_publisher_canonical_slug_passes(pr_factory):
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="classifier-judgment", status="completed", conclusion="neutral",
            started_at=None, completed_at=None, app_slug="github-actions",
            output_summary="Quadrant: A",
        ),
    ))
    assert ClassifierPublisherValidator().check(pr).passed


def test_classifier_publisher_wrong_slug_fails(pr_factory):
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="classifier-judgment", status="completed", conclusion="neutral",
            started_at=None, completed_at=None, app_slug="attacker-app",
            output_summary="Quadrant: A",
        ),
    ))
    r = ClassifierPublisherValidator().check(pr)
    assert not r.passed
    assert "attacker-app" in r.failure_reason


def test_classifier_publisher_missing_app_field_fails(pr_factory):
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="classifier-judgment", status="completed", conclusion="neutral",
            started_at=None, completed_at=None, app_slug=None,
            output_summary="Quadrant: A",
        ),
    ))
    r = ClassifierPublisherValidator().check(pr)
    assert not r.passed


def test_classifier_publisher_no_classifier_check_passes(pr_factory):
    # Absent classifier-judgment is fine; the engine treats absence as D
    # default, not as a publisher failure.
    pr = pr_factory(check_runs=())
    assert ClassifierPublisherValidator().check(pr).passed


def test_classifier_publisher_custom_slug(pr_factory):
    pr = pr_factory(check_runs=(
        CheckRunStatus(
            name="classifier-judgment", status="completed", conclusion="neutral",
            started_at=None, completed_at=None, app_slug="my-custom-app",
            output_summary="Quadrant: B",
        ),
    ))
    v = ClassifierPublisherValidator(publisher_slug="my-custom-app")
    assert v.check(pr).passed
