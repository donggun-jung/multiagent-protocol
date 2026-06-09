"""Classifier rule: read the *published* ``classifier-judgment`` verdict.

The fleet publishes a ``classifier-judgment`` check-run (from an external
script) carrying the authoritative quadrant for a PR. This rule reads that
check-run for the PR head SHA and **votes** the quadrant it declares into the
existing max-vote engine, so the owner's published judgment can RAISE a PR's
quadrant beyond what the path heuristic computed.

**Why a vote, not an override.** The classifier takes the MAXIMUM quadrant
(D > B > C > A). A vote from this rule can therefore only *raise* the verdict
toward owner control (e.g. path says A, published says D → final D), never
lower it (path says D, published says A → still D). That MAX-only-raises
invariant is the whole safety story: a forged or mistaken low verdict cannot
unlock a PR the heuristics already flagged.

**Provenance.** A check-run named ``classifier-judgment`` is honoured only when
its publisher identity matches the canonical app slug — the *same* identity
logic as :mod:`validator_classifier_publisher` (``env.classifier_publisher_slug``,
default ``github-actions``). Without this, anyone who can run a GitHub Actions
workflow in any repo could publish ``Quadrant: D`` to grief the gate (a forced
*raise* is only a denial-of-service, but provenance keeps even that out) — or,
worse, a future lowering path would be unguarded. We refuse non-canonical
publishers here too, so the door is guarded identically to the C-validator.

**Marker format.** The quadrant is read from the check-run's output/summary as
a line ``Quadrant: X`` (case-insensitive on the key; ``X`` is one of A/B/C/D,
optionally followed by more text). Example summary::

    Quadrant: D
    Reason: deletes src/multiagent_protocol/classifier.py

**Duplicate canonical judgments → MAXIMUM, never neutralize.** If more than one
``classifier-judgment`` run is published *by the canonical slug*, we take the
MAXIMUM quadrant across them (fail-safe toward owner control, QUADRANT_ORDER
A<C<B<D), never abstain-to-A. Abstaining on canonical duplicates would let a
second canonical judgment NEUTRALIZE a real ``Quadrant: D`` — a fail-OPEN bypass.
Non-canonical duplicates remain ignored (provenance is filtered first).

**Abstain (never raise an exception).** If no canonical ``classifier-judgment``
run is present, or every canonical run's summary is unparseable, this rule votes
the LOWEST quadrant (``A``) — i.e. it abstains, contributing nothing to the max.
It never crashes the tick.
"""

from __future__ import annotations

import re
from typing import cast

from multiagent_protocol.classifier import QUADRANT_ORDER
from multiagent_protocol.skills.base import (
    ClassifierVote,
    PRContext,
)
from multiagent_protocol.types import Quadrant

CLASSIFIER_JUDGMENT_CHECK = "classifier-judgment"

# A line like ``Quadrant: D`` (case-insensitive key, single A/B/C/D value).
_QUADRANT_LINE_RE = re.compile(r"^\s*Quadrant:\s*([ABCD])\b", re.IGNORECASE | re.MULTILINE)


class PublishedVerdictClassifier:
    name = "classifier_published_verdict"

    def __init__(self, publisher_slug: str | None = None) -> None:
        # Injected by the orchestrator with ``env.classifier_publisher_slug``.
        # ``None`` (the loader's 0-arg instance) trusts NO publisher, so it is a
        # safe no-op that always abstains; the configured instance does the real
        # read. We deliberately do NOT fall back to the GitHub-default slug for
        # ``None`` — an unconfigured rule must not silently honour verdicts.
        self.publisher_slug = publisher_slug

    def evaluate(self, pr_context: PRContext) -> ClassifierVote:
        if not self.publisher_slug:
            return self._abstain("no canonical publisher configured")

        # Provenance first: keep only ``classifier-judgment`` runs published by
        # the canonical slug (same identity gate as validator_classifier_publisher).
        # Non-canonical judgments are ignored entirely — a forged publisher must
        # not influence the verdict in either direction.
        canonical = [
            c for c in pr_context.check_runs
            if c.name == CLASSIFIER_JUDGMENT_CHECK
            and c.app_slug and c.app_slug == self.publisher_slug
        ]
        if not canonical:
            return self._abstain(
                "no canonical classifier-judgment check-run on head"
            )

        # >1 canonical judgment is NOT abstain-to-A: that would let a second
        # canonical judgment NEUTRALIZE a real ``Quadrant: D`` (fail-OPEN). Take
        # the MAXIMUM quadrant across all canonical, parseable judgments —
        # fail-safe toward owner control, since the max-vote engine only raises.
        quadrants = [
            q for q in (self._parse_quadrant(c.output_summary) for c in canonical)
            if q is not None
        ]
        if not quadrants:
            return self._abstain(
                "canonical classifier-judgment summary has no parseable "
                "'Quadrant: X' marker"
            )

        quadrant = max(quadrants, key=lambda q: QUADRANT_ORDER[q])
        suffix = "" if len(canonical) == 1 else f" (max of {len(quadrants)} canonical)"
        return ClassifierVote(
            quadrant=quadrant,
            reasoning=(
                f"published classifier-judgment verdict (Quadrant {quadrant}) "
                f"by canonical publisher '{self.publisher_slug}'{suffix}"
            ),
        )

    @staticmethod
    def _parse_quadrant(summary: str | None) -> Quadrant | None:
        if not summary:
            return None
        m = _QUADRANT_LINE_RE.search(summary)
        if m is None:
            return None
        # The regex only ever captures one of A/B/C/D, so the upper-cased group
        # is a valid Quadrant.
        return cast(Quadrant, m.group(1).upper())

    @staticmethod
    def _abstain(reason: str) -> ClassifierVote:
        # Lowest quadrant → contributes nothing to the max-vote engine.
        return ClassifierVote(quadrant="A", reasoning=f"abstain: {reason}")
