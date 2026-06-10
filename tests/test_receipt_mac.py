"""Unit tests for the shared HMAC layer (receipt_mac).

Covers compute/verify, constant-time comparison, tamper rejection, marker
embed/extract round-trip, the key-from-env reader, and the one-time
unsigned-fallback warning. The behavioural A3/A6 wiring (receipts + inbox
body) is tested in test_owner_approval_and_auto_revert.py / test_decision_inbox
/ test_inbox_resolution / test_vnext_security.
"""

from __future__ import annotations

import multiagent_protocol.receipt_mac as rm
from multiagent_protocol.receipt_mac import (
    KEY_ENV,
    MAC_MARKER_PREFIX,
    compute_mac,
    extract_mac,
    mac_key,
    mac_marker,
    verify_mac,
    warn_unsigned_once,
)

KEY = "test-secret-key"


# -- compute / verify --------------------------------------------------------

def test_compute_mac_is_deterministic_and_hex_sha256():
    a = compute_mac(KEY, "o/r", "42", "decision:approved-A", "h" * 40)
    b = compute_mac(KEY, "o/r", "42", "decision:approved-A", "h" * 40)
    assert a == b
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)


def test_compute_mac_matches_reference_hmac():
    import hashlib
    import hmac as _hmac
    parts = ("o/r", "42", "decision:approved-A", "h" * 40)
    expected = _hmac.new(
        KEY.encode(), "|".join(parts).encode(), hashlib.sha256
    ).hexdigest()
    assert compute_mac(KEY, *parts) == expected


def test_verify_mac_accepts_matching_digest():
    parts = ("o/r", "42", "ready-to-merge", "h" * 40)
    digest = compute_mac(KEY, *parts)
    assert verify_mac(KEY, digest, *parts)


def test_verify_mac_rejects_tampered_part():
    parts = ("o/r", "42", "ready-to-merge", "h" * 40)
    digest = compute_mac(KEY, *parts)
    # Any single field changed → no match (the | join prevents field-shift
    # collisions too).
    assert not verify_mac(KEY, digest, "o/r", "43", "ready-to-merge", "h" * 40)
    assert not verify_mac(KEY, digest, "evil/r", "42", "ready-to-merge", "h" * 40)
    assert not verify_mac(KEY, digest, "o/r", "42", "ready-to-merge", "e" * 40)
    assert not verify_mac(KEY, digest, "o/r", "42", "decision:approved-A", "h" * 40)


def test_verify_mac_rejects_wrong_key():
    parts = ("o/r", "42", "ready-to-merge", "h" * 40)
    digest = compute_mac(KEY, *parts)
    assert not verify_mac("other-key", digest, *parts)


def test_verify_mac_rejects_empty_or_missing_digest():
    parts = ("o/r", "42", "ready-to-merge", "h" * 40)
    assert not verify_mac(KEY, "", *parts)
    assert not verify_mac(KEY, None, *parts)  # type: ignore[arg-type]


def test_verify_mac_uses_constant_time_compare(monkeypatch):
    # The verify path must route through hmac.compare_digest (constant-time),
    # never a plain == on the digests. Patch compare_digest to observe the call
    # and assert it received the expected recomputed digest.
    seen: list[tuple[str, str]] = []
    real = rm.hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(rm.hmac, "compare_digest", spy)
    parts = ("o/r", "42", "ready-to-merge", "h" * 40)
    digest = compute_mac(KEY, *parts)
    assert verify_mac(KEY, digest, *parts)
    assert seen and seen[0][0] == digest and seen[0][1] == digest


def test_field_join_prevents_ambiguity():
    # ("ab","c") and ("a","bc") must NOT produce the same MAC — the "|"
    # separator disambiguates concatenation.
    assert compute_mac(KEY, "ab", "c") != compute_mac(KEY, "a", "bc")


# -- marker embed / extract --------------------------------------------------

def test_mac_marker_round_trips():
    digest = compute_mac(KEY, "o/r", "42", "ready-to-merge", "h" * 40)
    marker = mac_marker(digest)
    assert marker.startswith(MAC_MARKER_PREFIX) and marker.endswith("-->")
    body = f"some receipt text\n\n{marker}\n"
    assert extract_mac(body) == digest


def test_extract_mac_absent_returns_none():
    assert extract_mac("a receipt body with no mac marker at all") is None


def test_extract_mac_requires_closing_tag():
    # A line missing the closing --> must NOT yield a value (mirrors the other
    # marker parsers; an attacker cannot defeat detection by stripping `-->`).
    body = f"{MAC_MARKER_PREFIX} deadbeef \n"  # no closing -->
    assert extract_mac(body) is None


def test_extract_mac_last_well_formed_marker_wins():
    body = (
        f"{mac_marker('a' * 64)}\n"
        f"{mac_marker('b' * 64)}\n"
    )
    assert extract_mac(body) == "b" * 64


# -- key from env ------------------------------------------------------------

def test_mac_key_reads_env(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "from-env")
    assert mac_key() == "from-env"


def test_mac_key_none_when_unset(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    assert mac_key() is None


def test_mac_key_empty_is_none(monkeypatch):
    # An empty string is not a real secret → treated as unset.
    monkeypatch.setenv(KEY_ENV, "")
    assert mac_key() is None


# -- one-time unsigned-fallback warning --------------------------------------

def test_warn_unsigned_once_fires_exactly_once(monkeypatch, caplog):
    # Reset the module latch so this test is order-independent.
    monkeypatch.setattr(rm, "_warned_unsigned", False)
    with caplog.at_level("WARNING"):
        warn_unsigned_once()
        warn_unsigned_once()
        warn_unsigned_once()
    hits = [r for r in caplog.records if KEY_ENV in r.message and "unsigned" in r.message]
    assert len(hits) == 1
    assert "set it before enforce-mode go-live" in hits[0].message
