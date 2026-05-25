"""Tests for the 4-quadrant classifier engine."""

from __future__ import annotations

from pathlib import Path

from multiagent_protocol.classifier import classify
from multiagent_protocol.skills.base import ClassifierVote
from multiagent_protocol.types import FileChange, PRContext


def _make_pr_context(
    files: tuple[FileChange, ...] = (),
    number: int = 1,
) -> PRContext:
    return PRContext(
        repo_owner="example",
        repo_name="repo",
        number=number,
        title="t",
        body="b",
        head_sha="h" * 40,
        base_sha="b" * 40,
        base_ref="main",
        state="open",
        merged=False,
        labels=(),
        author_login="alice",
        commits=(),
        files_changed=files,
        check_runs=(),
        label_events=(),
    )


class _Rule:
    def __init__(self, name: str, quadrant: str, reasoning: str = "") -> None:
        self.name = name
        self._quadrant = quadrant
        self._reasoning = reasoning

    def evaluate(self, pr_context):
        return ClassifierVote(quadrant=self._quadrant, reasoning=self._reasoning)


def test_no_rules_returns_a():
    v = classify(_make_pr_context(), rules=[])
    assert v.quadrant == "A"


def test_single_rule_a():
    v = classify(_make_pr_context(), rules=[_Rule("r", "A")])
    assert v.quadrant == "A"


def test_maximum_quadrant_takes_d_over_a():
    rules = [_Rule("a", "A"), _Rule("d", "D"), _Rule("b", "B")]
    v = classify(_make_pr_context(), rules=rules)
    assert v.quadrant == "D"


def test_b_beats_c():
    # B > C in our ordering because B = "reversible+critical" deserves more
    # scrutiny than C = "irreversible+non-critical" — both produce audit
    # but B's audit is louder.
    rules = [_Rule("c", "C"), _Rule("b", "B")]
    v = classify(_make_pr_context(), rules=rules)
    assert v.quadrant == "B"


def test_votes_recorded_in_order():
    rules = [_Rule("first", "A", "r1"), _Rule("second", "C", "r2")]
    v = classify(_make_pr_context(), rules=rules)
    assert len(v.votes) == 2
    assert v.votes[0][0] == "first"
    assert v.votes[1][0] == "second"
    assert v.votes[1][2] == "r2"


def test_audit_log_appended(tmp_path: Path):
    log = tmp_path / "audit.jsonl"
    v = classify(_make_pr_context(), rules=[_Rule("r", "B", "why")], audit_log_path=log)
    assert log.exists()
    content = log.read_text(encoding="utf-8")
    assert content.count("\n") == 1
    assert "\"quadrant\":\"B\"" in content
    assert "\"why\"" in content
    assert v.quadrant == "B"


def test_user_rule_voting_a_cannot_lower_builtin_d():
    builtin = _Rule("builtin", "D")
    user = _Rule("user", "A")
    v = classify(_make_pr_context(), rules=[builtin, user])
    assert v.quadrant == "D"
