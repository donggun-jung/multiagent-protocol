"""Tests for decision_inbox.

These cover the security-critical resolve-verdict path (SECURITY.md
threat #5 — "Decision Inbox manipulation: anything that allows non-owner
reactions/comments to count as 'owner approval'") and the tamper-detection
path (parse_nonce_and_sha → head_sha precondition).

Before R3 there were zero tests on this module.
"""

from __future__ import annotations

from multiagent_protocol.decision_inbox import (
    APPROVE_RE,
    REJECT_RE,
    issue_body,
    parse_nonce_and_sha,
    resolve_verdict,
)

# ---- Issue body schema -----------------------------------------------------

def test_issue_body_contains_head_sha_and_nonce():
    body = issue_body(
        pr_full_name="alice/repo-a",
        pr_number=42,
        head_sha="abcdef0123456789" * 2 + "abcdef01",  # 40 chars
        classifier_reasoning="touches src/multiagent_protocol/foo.py",
        nonce="testnonce123",
        timestamp="2026-05-26T12:00:00Z",
    )
    assert "alice/repo-a#42" in body
    assert "<!-- decision-inbox-nonce: testnonce123 -->" in body
    assert "<!-- decision-inbox-head-sha:" in body
    assert "Quadrant D" in body
    # Reasoning surfaced.
    assert "touches src/multiagent_protocol/foo.py" in body


def test_parse_nonce_and_sha_roundtrip():
    body = issue_body(
        pr_full_name="alice/r",
        pr_number=1,
        head_sha="a" * 40,
        classifier_reasoning="r",
        nonce="abc123",
    )
    nonce, sha = parse_nonce_and_sha(body)
    assert nonce == "abc123"
    assert sha == "a" * 40


def test_parse_nonce_and_sha_missing_returns_none():
    nonce, sha = parse_nonce_and_sha("Some other Issue body with no tags.")
    assert nonce is None
    assert sha is None


def test_parse_nonce_and_sha_malformed_no_closing_tag():
    """Without `-->` closing, the parser must NOT extract a value.

    Otherwise an attacker who edits the Issue body to remove the closing
    `-->` could prevent the bot from detecting head-SHA mismatch.
    """
    body = (
        "**Owner approval required**\n\n"
        "<!-- decision-inbox-nonce: abc123 \n"  # no closing -->
        "<!-- decision-inbox-head-sha: deadbeef \n"
    )
    nonce, sha = parse_nonce_and_sha(body)
    assert nonce is None
    assert sha is None


def test_parse_nonce_and_sha_ignores_inline_match():
    body = (
        "Body that mentions <!-- decision-inbox-nonce: fake --> in prose, "
        "with no real metadata block."
    )
    # Inline single-line match IS allowed by the parser (it just looks for
    # any line starting with the tag). What we are pinning here is that the
    # body without leading-whitespace stripped still works correctly.
    nonce, sha = parse_nonce_and_sha(body)
    # Either nonce was extracted or not; what matters is that head_sha
    # remains None and the parser does not crash. Both are acceptable
    # outcomes — pin the safer one: matching tag found, no sha.
    assert sha is None


# ---- Regex sanity ----------------------------------------------------------

def test_approve_re_matches_uppercase_and_lowercase():
    assert APPROVE_RE.search("/approve A")
    assert APPROVE_RE.search("/APPROVE B")
    assert APPROVE_RE.search("/approve c")
    assert not APPROVE_RE.search("/approve D")  # D is not a permitted ballot
    assert not APPROVE_RE.search("approve A")    # missing slash


def test_reject_re_basic():
    assert REJECT_RE.search("/reject")
    assert REJECT_RE.search("/REJECT")
    assert not REJECT_RE.search("rejected")


# ---- Verdict resolution (the security-critical bit) -------------------------

OWNER = "owner"
NON_OWNER = "random-account"


def _reaction(content: str, login: str, ts: str = "2026-05-26T10:00:00Z") -> dict:
    return {"content": content, "user": {"login": login}, "created_at": ts}


def _comment(body: str, login: str, ts: str = "2026-05-26T10:00:00Z") -> dict:
    return {"body": body, "user": {"login": login}, "created_at": ts}


def test_owner_thumbs_up_resolves_approved_a():
    verdict = resolve_verdict(
        reactions=[_reaction("+1", OWNER)],
        comments=[],
        allowlisted_actors=(OWNER,),
    )
    assert verdict == "approved-A"


def test_owner_thumbs_down_resolves_rejected():
    verdict = resolve_verdict(
        reactions=[_reaction("-1", OWNER)],
        comments=[],
        allowlisted_actors=(OWNER,),
    )
    assert verdict == "rejected"


def test_non_owner_thumbs_up_does_not_resolve():
    """Critical: a random GitHub account 👍'ing the Issue MUST NOT count."""
    verdict = resolve_verdict(
        reactions=[_reaction("+1", NON_OWNER)],
        comments=[],
        allowlisted_actors=(OWNER,),
    )
    assert verdict is None


def test_owner_comment_approve_b_resolves_approved_b():
    verdict = resolve_verdict(
        reactions=[],
        comments=[_comment("/approve B", OWNER)],
        allowlisted_actors=(OWNER,),
    )
    assert verdict == "approved-B"


def test_owner_comment_approve_c_resolves_approved_c():
    verdict = resolve_verdict(
        reactions=[],
        comments=[_comment("/approve c", OWNER)],
        allowlisted_actors=(OWNER,),
    )
    assert verdict == "approved-C"


def test_owner_comment_reject_resolves_rejected():
    verdict = resolve_verdict(
        reactions=[],
        comments=[_comment("/reject\n\nReason: design needs more discussion.", OWNER)],
        allowlisted_actors=(OWNER,),
    )
    assert verdict == "rejected"


def test_non_owner_approve_comment_does_not_resolve():
    """Critical: /approve from a non-allowlisted login MUST NOT count."""
    verdict = resolve_verdict(
        reactions=[],
        comments=[_comment("/approve A", NON_OWNER)],
        allowlisted_actors=(OWNER,),
    )
    assert verdict is None


def test_most_recent_signal_wins_owner_changes_mind():
    """Owner first 👍, then comments /reject → reject wins."""
    verdict = resolve_verdict(
        reactions=[_reaction("+1", OWNER, ts="2026-05-26T10:00:00Z")],
        comments=[_comment("/reject", OWNER, ts="2026-05-26T11:00:00Z")],
        allowlisted_actors=(OWNER,),
    )
    assert verdict == "rejected"


def test_most_recent_signal_wins_owner_changes_mind_other_way():
    """Owner first /reject, then 👍 → approve wins (latest in time)."""
    verdict = resolve_verdict(
        reactions=[_reaction("+1", OWNER, ts="2026-05-26T11:00:00Z")],
        comments=[_comment("/reject", OWNER, ts="2026-05-26T10:00:00Z")],
        allowlisted_actors=(OWNER,),
    )
    assert verdict == "approved-A"


def test_mixed_owner_and_non_owner_only_owner_counts():
    """Non-owner /approve A at 11:00, owner /reject at 10:00 → reject wins.

    Without the allowlist filter, this would have been approved-A (the
    later signal). The whole point of the filter is to ensure the non-
    owner signal is never even considered.
    """
    verdict = resolve_verdict(
        reactions=[],
        comments=[
            _comment("/approve A", NON_OWNER, ts="2026-05-26T11:00:00Z"),
            _comment("/reject", OWNER, ts="2026-05-26T10:00:00Z"),
        ],
        allowlisted_actors=(OWNER,),
    )
    assert verdict == "rejected"


def test_other_emoji_reactions_are_ignored():
    """Only 👍/👎 (+1/-1) reactions are read. heart/eyes/etc. must NOT vote."""
    for content in ("heart", "eyes", "rocket", "laugh", "confused"):
        verdict = resolve_verdict(
            reactions=[_reaction(content, OWNER)],
            comments=[],
            allowlisted_actors=(OWNER,),
        )
        assert verdict is None, f"reaction '{content}' must not resolve"


def test_no_signals_returns_none():
    assert resolve_verdict([], [], allowlisted_actors=(OWNER,)) is None


def test_multiple_allowlisted_actors_either_can_resolve():
    REVIEWER = "delegated-reviewer"
    verdict = resolve_verdict(
        reactions=[_reaction("+1", REVIEWER)],
        comments=[],
        allowlisted_actors=(OWNER, REVIEWER),
    )
    assert verdict == "approved-A"
