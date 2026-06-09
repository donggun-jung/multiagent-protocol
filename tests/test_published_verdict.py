"""Tests for the R2 published-classifier-verdict rule.

The rule reads the ``classifier-judgment`` check-run, verifies the publisher
identity (same gate as validator_classifier_publisher), parses ``Quadrant: X``,
and VOTES it. Because the classifier takes the MAX quadrant, a published verdict
can only RAISE the verdict, never lower it. Anything wrong → abstain (vote A),
never raise an exception.
"""

from __future__ import annotations

from multiagent_protocol.classifier import classify
from multiagent_protocol.skills.builtin.classifier_path_default import (
    PathDefaultClassifier,
)
from multiagent_protocol.skills.builtin.classifier_published_verdict import (
    PublishedVerdictClassifier,
)
from multiagent_protocol.types import CheckRunStatus, FileChange

SLUG = "github-actions"


def _judgment(summary: str, *, name: str = "classifier-judgment",
              slug: str | None = SLUG) -> CheckRunStatus:
    return CheckRunStatus(
        name=name, status="completed", conclusion="neutral",
        started_at=None, completed_at=None, app_slug=slug, output_summary=summary,
    )


# -- rule unit: vote / abstain ------------------------------------------------

def test_published_d_by_canonical_slug_votes_d(pr_factory):
    pr = pr_factory(check_runs=(_judgment("Quadrant: D\nReason: irreversible"),))
    v = PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr)
    assert v.quadrant == "D"


def test_published_each_quadrant_parsed(pr_factory):
    for q in ("A", "B", "C", "D"):
        pr = pr_factory(check_runs=(_judgment(f"Quadrant: {q}"),))
        assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == q


def test_wrong_slug_is_ignored_abstains(pr_factory):
    pr = pr_factory(check_runs=(_judgment("Quadrant: D", slug="malicious-app"),))
    v = PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr)
    assert v.quadrant == "A"  # abstain — does not honour a non-canonical publisher
    assert "abstain" in v.reasoning


def test_missing_slug_is_ignored_abstains(pr_factory):
    pr = pr_factory(check_runs=(_judgment("Quadrant: D", slug=None),))
    assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == "A"


def test_absent_check_run_abstains(pr_factory):
    pr = pr_factory(check_runs=())
    assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == "A"


def test_garbled_summary_abstains(pr_factory):
    pr = pr_factory(check_runs=(_judgment("no quadrant marker here"),))
    assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == "A"


def test_empty_summary_abstains(pr_factory):
    pr = pr_factory(check_runs=(_judgment(""),))
    assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == "A"


def test_invalid_quadrant_letter_abstains(pr_factory):
    # 'Quadrant: Z' is not a valid quadrant → no match → abstain.
    pr = pr_factory(check_runs=(_judgment("Quadrant: Z"),))
    assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == "A"


def test_unconfigured_rule_abstains(pr_factory):
    # 0-arg loader instance (publisher_slug=None) trusts no publisher.
    pr = pr_factory(check_runs=(_judgment("Quadrant: D"),))
    assert PublishedVerdictClassifier().evaluate(pr).quadrant == "A"


def test_multiple_canonical_judgments_take_maximum(pr_factory):
    # R2 fix: two CANONICAL classifier-judgment runs [A, D]. Abstaining (old
    # behavior) would let the second canonical judgment NEUTRALIZE a real
    # Quadrant: D — a fail-OPEN bypass. Take the MAXIMUM quadrant (A<C<B<D) →
    # votes D, fail-safe toward owner control.
    pr = pr_factory(check_runs=(_judgment("Quadrant: A"), _judgment("Quadrant: D")))
    v = PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr)
    assert v.quadrant == "D"


def test_multiple_canonical_judgments_d_plus_garbled_takes_d(pr_factory):
    # [D, garbled]: the garbled run contributes no parseable quadrant, but the
    # real D is still honoured (max over the parseable canonical judgments).
    pr = pr_factory(check_runs=(
        _judgment("Quadrant: D"),
        _judgment("no quadrant marker here"),
    ))
    assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == "D"


def test_zero_canonical_judgments_abstains(pr_factory):
    # All classifier-judgment runs are non-canonical (wrong slug) → no canonical
    # judgment → abstain (votes A), as before.
    pr = pr_factory(check_runs=(
        _judgment("Quadrant: D", slug="evil-a"),
        _judgment("Quadrant: D", slug="evil-b"),
    ))
    v = PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr)
    assert v.quadrant == "A"
    assert "abstain" in v.reasoning


def test_all_canonical_judgments_unparseable_abstains(pr_factory):
    # Two canonical runs, neither with a parseable Quadrant marker → abstain.
    pr = pr_factory(check_runs=(
        _judgment("garbage one"),
        _judgment("garbage two"),
    ))
    v = PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr)
    assert v.quadrant == "A"
    assert "abstain" in v.reasoning


def test_non_canonical_duplicate_does_not_lower_canonical(pr_factory):
    # A canonical D plus a NON-canonical (wrong-slug) A: the non-canonical run is
    # ignored entirely, so the canonical D stands (a forged publisher cannot
    # neutralize a real verdict).
    pr = pr_factory(check_runs=(
        _judgment("Quadrant: D"),
        _judgment("Quadrant: A", slug="forged"),
    ))
    assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == "D"


def test_quadrant_marker_case_insensitive_key(pr_factory):
    pr = pr_factory(check_runs=(_judgment("quadrant: d"),))
    assert PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr).quadrant == "D"


# -- interaction with the max-vote engine -------------------------------------

def test_published_d_raises_path_a(pr_factory):
    # Path heuristic says A (doc change); published verdict says D → final D.
    pr = pr_factory(
        files_changed=(FileChange("README.md", "modified", 1, 0),),
        check_runs=(_judgment("Quadrant: D"),),
    )
    rules = [PathDefaultClassifier(), PublishedVerdictClassifier(publisher_slug=SLUG)]
    verdict = classify(pr, rules)
    assert verdict.quadrant == "D"


def test_published_a_cannot_lower_path_d(pr_factory):
    # Path heuristic says D (src deletion); published verdict says A. Max-vote
    # keeps it D — the published vote can never LOWER.
    pr = pr_factory(
        files_changed=(FileChange("src/multiagent_protocol/x.py", "removed", 0, 9),),
        check_runs=(_judgment("Quadrant: A"),),
    )
    rules = [PathDefaultClassifier(), PublishedVerdictClassifier(publisher_slug=SLUG)]
    verdict = classify(pr, rules)
    assert verdict.quadrant == "D"


def test_wrong_slug_cannot_raise(pr_factory):
    # A 'Quadrant: D' published by the wrong slug is ignored, so a path-A PR
    # stays A (no raise, no crash).
    pr = pr_factory(
        files_changed=(FileChange("README.md", "modified", 1, 0),),
        check_runs=(_judgment("Quadrant: D", slug="evil"),),
    )
    rules = [PathDefaultClassifier(), PublishedVerdictClassifier(publisher_slug=SLUG)]
    verdict = classify(pr, rules)
    assert verdict.quadrant == "A"
