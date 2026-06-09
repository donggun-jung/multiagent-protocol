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


def test_multiple_judgment_checks_abstain(pr_factory):
    pr = pr_factory(check_runs=(_judgment("Quadrant: D"), _judgment("Quadrant: A")))
    v = PublishedVerdictClassifier(publisher_slug=SLUG).evaluate(pr)
    assert v.quadrant == "A"
    assert "ambiguous" in v.reasoning


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
