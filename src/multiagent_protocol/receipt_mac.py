"""Shared HMAC layer for bot-authored approval artifacts (A3 + A6).

Approvals are carried in two mutable surfaces — the PR receipt comment
(:mod:`multiagent_protocol.label_provenance`) and the Decision Inbox issue
body (:mod:`multiagent_protocol.decision_inbox`) — and were authenticated
*only* by author identity (``user.login == bot_user``). Two leaks defeat that:

  A3 — a leaked installation token can POST a bot-authored receipt comment
       binding any label to the current head SHA (forging C3 on a Quadrant-D
       PR). There is no signature.
  A6 — anyone who can edit a pending inbox issue body can rewrite the
       authoritative PR ref / head SHA and redirect a legitimate owner
       approval to a different PR/head.

This module adds a keyed MAC over the authoritative fields of each artifact,
embedded in an HTML-comment marker. The key (``MERGE_GATE_RECEIPT_KEY``) is a
NEW secret distinct from the App credentials, so a leaked App token alone can
no longer mint a *valid* artifact — it can post a comment, but not one that
carries a correct MAC.

**Graceful fallback (do NOT break unsigned deployments).** When the key is
unset the gate logs ONE loud warning per process and falls back to the prior
author-only behaviour. When the key IS set the verify side fails **closed**:
any artifact whose marker is missing or whose MAC does not recompute is
rejected (it does not count), exactly mirroring how the rest of the gate
treats unverifiable provenance.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

logger = logging.getLogger(__name__)

KEY_ENV = "MERGE_GATE_RECEIPT_KEY"

# HTML-comment marker carrying the hex MAC, e.g.
# ``<!-- merge-gate-mac: <hexdigest> -->``. Same shape as the inbox issue's
# ``<!-- decision-inbox-head-sha: ... -->`` and the receipt's label/sha
# markers, so it survives GitHub's Markdown rendering invisibly.
MAC_MARKER_PREFIX = "<!-- merge-gate-mac:"
MAC_MARKER_SUFFIX = "-->"

# One-loud-warning-per-process latch (the unsigned-fallback notice). A stateless
# cron tick still warns once each run; a longer-lived process warns once total.
_warned_unsigned = False


def mac_key() -> str | None:
    """The MAC secret from ``MERGE_GATE_RECEIPT_KEY`` (None if unset/empty).

    A None return means "no key configured" — every caller then logs the
    one-time unsigned-fallback warning (:func:`warn_unsigned_once`) and keeps
    the prior author-only behaviour. A present-but-empty value is treated as
    unset (an empty HMAC key is never a real secret).
    """
    value = os.environ.get(KEY_ENV)
    return value if value else None


def warn_unsigned_once() -> None:
    """Emit the unsigned-fallback warning ONCE per process.

    Called from each MAC-aware write/read site when no key is configured, so
    the operator is told loudly (but not on every tick line) that approval
    artifacts are running author-only. Idempotent within a process.
    """
    global _warned_unsigned
    if _warned_unsigned:
        return
    _warned_unsigned = True
    logger.warning(
        "%s unset — approval receipts are unsigned (author-only auth); "
        "set it before enforce-mode go-live",
        KEY_ENV,
    )


def compute_mac(key: str, *parts: str) -> str:
    """HMAC-SHA256 hex digest over ``"|".join(parts)`` keyed by ``key``.

    The parts are the artifact's authoritative fields in a FIXED order (the
    caller pins that order); joining with ``|`` keeps distinct field tuples
    from colliding. Returns the lowercase hex digest.
    """
    return hmac.new(
        key.encode(), "|".join(parts).encode(), hashlib.sha256
    ).hexdigest()


def verify_mac(key: str, expected_hexdigest: str, *parts: str) -> bool:
    """True iff ``expected_hexdigest`` matches the MAC recomputed over ``parts``.

    Constant-time comparison (:func:`hmac.compare_digest`) so a near-miss
    forgery cannot be narrowed by timing. A missing/empty ``expected_hexdigest``
    (or any value that does not recompute exactly) returns False → the caller
    fails closed.
    """
    if not expected_hexdigest:
        return False
    return hmac.compare_digest(expected_hexdigest, compute_mac(key, *parts))


def mac_marker(hexdigest: str) -> str:
    """The HTML-comment marker line embedding ``hexdigest`` in an artifact."""
    return f"{MAC_MARKER_PREFIX} {hexdigest} {MAC_MARKER_SUFFIX}"


def extract_mac(text: str) -> str | None:
    """Read the MAC hex digest out of an artifact's body, else None.

    Mirrors the other marker parsers in the codebase: a line is honoured only
    when it both starts with the prefix AND ends with the closing ``-->`` (so
    an attacker cannot defeat detection by stripping the closing tag). The
    LAST well-formed marker wins (the writer appends exactly one). None when
    no well-formed marker is present → the caller fails closed when a key is
    set.
    """
    found: str | None = None
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith(MAC_MARKER_PREFIX) and s.endswith(MAC_MARKER_SUFFIX):
            found = (
                s.removeprefix(MAC_MARKER_PREFIX)
                .removesuffix(MAC_MARKER_SUFFIX)
                .strip()
            )
    return found
