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

def test_ready_to_merge_label_present_with_matching_receipt(pr_factory):
    # Receipt-required contract (vNext): the label opens C1 only with a bot
    # receipt bound to the current head ("h"*40 default).
    pr = pr_factory(labels=("ready-to-merge",))
    v = ReadyToMergeValidator(approved_shas={"ready-to-merge": "h" * 40})
    assert v.check(pr).passed


def test_ready_to_merge_label_present_no_receipt_fails(pr_factory):
    # The label alone (no bot receipt) no longer opens C1 — mirrors C3. The
    # runtime records a receipt this tick; with no receipt map (None) C1 fails.
    pr = pr_factory(labels=("ready-to-merge",))
    r = ReadyToMergeValidator().check(pr)
    assert not r.passed
    assert "no current-head receipt" in r.failure_reason
    # An explicitly empty receipt map behaves identically (None == {}).
    r2 = ReadyToMergeValidator(approved_shas={}).check(pr)
    assert not r2.passed
    assert "no current-head receipt" in r2.failure_reason


def test_ready_to_merge_label_absent(pr_factory):
    pr = pr_factory(labels=())
    r = ReadyToMergeValidator().check(pr)
    assert not r.passed
    assert "ready-to-merge" in r.failure_reason


def test_ready_to_merge_with_allowlist_actor(pr_factory):
    # Receipt at the current head + an allowlisted applier → C1 passes (the
    # actor check runs on top of the matching receipt).
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
    v = ReadyToMergeValidator(allowlisted_actors=("owner",),
                              approved_shas={"ready-to-merge": "h" * 40})
    assert v.check(pr).passed


def test_ready_to_merge_label_applied_by_non_allowlisted_actor(pr_factory):
    # Defense-in-depth: even WITH a matching current-head receipt, a label
    # applied only by a non-allowlisted actor fails the applier check (mirrors
    # owner_approval's receipt-does-not-bless-untrusted-applier).
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
    v = ReadyToMergeValidator(allowlisted_actors=("owner",),
                              approved_shas={"ready-to-merge": "h" * 40})
    r = v.check(pr)
    assert not r.passed
    assert "allowlisted actor" in r.failure_reason


def test_ready_to_merge_stale_sha_bound_label_fails(pr_factory):
    # SHA binding (vNext): the bot recorded ready-to-merge at SHA1 (receipt);
    # the head is now SHA2 → the label is stale and C1 fails, regardless of
    # who applied it or any commit timestamps.
    pr = pr_factory(
        labels=("ready-to-merge",), head_sha="2" * 40,
        label_events=(
            LabelEvent(label="ready-to-merge", actor_login="owner",
                       created_at="2026-05-25T00:00:00Z"),
        ),
    )
    v = ReadyToMergeValidator(allowlisted_actors=("owner",),
                              approved_shas={"ready-to-merge": "1" * 40})
    r = v.check(pr)
    assert not r.passed
    assert "stale" in r.failure_reason


def test_ready_to_merge_sha_bound_label_matching_head_passes(pr_factory):
    # Re-recorded against the current head → the veto does not fire and the
    # normal allowlisted-actor check decides.
    pr = pr_factory(
        labels=("ready-to-merge",), head_sha="2" * 40,
        label_events=(
            LabelEvent(label="ready-to-merge", actor_login="owner",
                       created_at="2026-05-25T00:00:00Z"),
        ),
    )
    v = ReadyToMergeValidator(allowlisted_actors=("owner",),
                              approved_shas={"ready-to-merge": "2" * 40})
    assert v.check(pr).passed


def test_ready_to_merge_stale_binding_vetoes_even_with_empty_allowlist(pr_factory):
    # The veto applies before the relaxed empty-allowlist early-pass.
    pr = pr_factory(labels=("ready-to-merge",), head_sha="2" * 40)
    v = ReadyToMergeValidator(approved_shas={"ready-to-merge": "1" * 40})
    assert not v.check(pr).passed


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


# -- Publisher trust for required checks (C2) ---------------------------------

def _check_from(name: str, conclusion: str, slug: str | None) -> CheckRunStatus:
    return CheckRunStatus(
        name=name, status="completed", conclusion=conclusion,
        started_at=None, completed_at=None, app_slug=slug, output_summary=None,
    )


def test_ci_green_required_green_from_unexpected_publisher_fails(pr_factory):
    # A green run named like the required check but published by a DIFFERENT
    # App must not satisfy C2 — treated as "not yet green" (fail closed).
    pr = pr_factory(check_runs=(_check_from("test", "success", "attacker-app"),))
    v = CiGreenValidator(required_checks=("test",),
                         expected_check_publisher="github-actions")
    r = v.check(pr)
    assert not r.passed
    assert "expected app 'github-actions'" in r.failure_reason
    assert "attacker-app" in r.failure_reason


def test_ci_green_required_green_from_expected_publisher_passes(pr_factory):
    pr = pr_factory(check_runs=(_check_from("test", "success", "github-actions"),))
    v = CiGreenValidator(required_checks=("test",),
                         expected_check_publisher="github-actions")
    assert v.check(pr).passed


def test_ci_green_required_missing_app_slug_fails_when_publisher_expected(pr_factory):
    # Mirrors validator_classifier_publisher: a run with no 'app' field cannot
    # prove its publisher identity → untrusted → fail closed.
    pr = pr_factory(check_runs=(_check_from("test", "success", None),))
    v = CiGreenValidator(required_checks=("test",),
                         expected_check_publisher="github-actions")
    r = v.check(pr)
    assert not r.passed
    assert "<missing app>" in r.failure_reason


def test_ci_green_required_expected_plus_foreign_green_passes(pr_factory):
    # One green run from the expected publisher satisfies C2; an extra green
    # run from a foreign app neither helps nor hurts.
    pr = pr_factory(check_runs=(
        _check_from("test", "success", "github-actions"),
        _check_from("test", "success", "some-other-app"),
    ))
    v = CiGreenValidator(required_checks=("test",),
                         expected_check_publisher="github-actions")
    assert v.check(pr).passed


def test_ci_green_required_foreign_failure_still_blocks(pr_factory):
    # The publisher gate only restricts who can SATISFY a required check; a
    # same-named failing run from any publisher still blocks (never relaxed).
    pr = pr_factory(check_runs=(
        _check_from("test", "success", "github-actions"),
        _check_from("test", "failure", "attacker-app"),
    ))
    v = CiGreenValidator(required_checks=("test",),
                         expected_check_publisher="github-actions")
    assert not v.check(pr).passed


def test_ci_green_no_expected_publisher_keeps_legacy_behavior(pr_factory):
    # Direct construction without expected_check_publisher: a green required
    # check passes regardless of publisher (pre-hardening behavior).
    pr = pr_factory(check_runs=(_check_from("test", "success", "whatever-app"),))
    assert CiGreenValidator(required_checks=("test",)).check(pr).passed


# -- Publisher trust on the LEGACY no-required-checks path (C2) ---------------

def test_ci_green_legacy_foreign_green_only_fails_when_publisher_expected(pr_factory):
    # No NAMED required checks (legacy strict-all-green path). The only green
    # run is from a foreign App → with an expected publisher set, C2 fails
    # closed: a foreign green must not satisfy C2 even with no named checks.
    pr = pr_factory(check_runs=(_check_from("ci", "success", "attacker-app"),))
    v = CiGreenValidator(expected_check_publisher="github-actions")
    r = v.check(pr)
    assert not r.passed
    assert "expected app 'github-actions'" in r.failure_reason
    assert "attacker-app" in r.failure_reason


def test_ci_green_legacy_expected_publisher_green_passes(pr_factory):
    # A green run from the expected App on the legacy path satisfies C2.
    pr = pr_factory(check_runs=(_check_from("ci", "success", "github-actions"),))
    v = CiGreenValidator(expected_check_publisher="github-actions")
    assert v.check(pr).passed


def test_ci_green_legacy_expected_plus_foreign_green_passes(pr_factory):
    # One green from the expected App is enough; an extra foreign green is
    # neither necessary nor harmful (all completed runs are still success).
    pr = pr_factory(check_runs=(
        _check_from("ci", "success", "github-actions"),
        _check_from("ci", "success", "some-other-app"),
    ))
    v = CiGreenValidator(expected_check_publisher="github-actions")
    assert v.check(pr).passed


def test_ci_green_legacy_publisher_gate_not_applied_when_unset(pr_factory):
    # With no expected publisher (None), the legacy path is unchanged: a green
    # run from any App passes.
    pr = pr_factory(check_runs=(_check_from("ci", "success", "whatever-app"),))
    assert CiGreenValidator().check(pr).passed


def test_ci_green_legacy_allow_no_checks_unaffected_by_publisher(pr_factory):
    # A head with ZERO check-runs under allow_no_checks still passes vacuously
    # even with an expected publisher set — there is no run to attribute.
    pr = pr_factory(check_runs=())
    v = CiGreenValidator(allow_no_checks=True,
                         expected_check_publisher="github-actions")
    assert v.check(pr).passed


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
