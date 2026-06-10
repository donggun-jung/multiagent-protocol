"""Default path-based classifier rule.

Reads ``config/classifier_rules.yml`` if present; otherwise applies a
conservative built-in heuristic:

- Any change touching ``src/`` or ``schemas/`` or ``docs/concepts/`` or
  ``.github/workflows/`` or ``config/`` or ``governance/`` is **critical**.
- Any change deleting files is **irreversible**.
- The cross-product yields A/B/C/D as in ``docs/concepts/four-quadrants.md``.

``config/`` is critical because a PR editing the gate's OWN config (adding a
repo to ``audit_only_repos``, changing ``required_checks``, or editing the agent
registry) reconfigures enforcement itself — such a change must be at least
critical (→ B/D, owner-visible), never auto-merged as Quadrant A.

``governance/`` is critical for the same reason: it holds the gate's own
decision logic (``governance/scripts/``, ``governance/REVERSIBILITY_RUBRIC.md``,
``governance/schemas/``). A PR rewriting the gate's rules must route to the
owner (Quadrant B/D), never auto-approve itself.

ALWAYS-D (the self-containment guarantee). A subset of the critical paths govern
*enforcement itself* — the operating doctrine (``docs/concepts/``, Tier 3), the
gate configuration (``config/``), the gate's decision logic (``governance/``),
the CI runtime + workflow definitions (``.github/workflows/``), the JSON schemas
(``schemas/``), and the audit/receipt state (``bot-state/``, Tier 5). Per the
five-tier taxonomy these are "**always Quadrant D regardless of the diff**": a
MODIFY (not only a delete) must route to the owner. The earlier rule only forced
D on a *deletion*, so an agent could *rewrite* the rules that constrain it and
have the change auto-merge as Quadrant B — a self-referential containment breach
(both the correctness and security review flagged this #1; GPT-5.5 confirmed it
should block enforce-mode). ALWAYS-D closes it.

Tier 2 (immutable records, ``docs/decisions/`` ADRs + ``docs/meetings/``) is
append-only: MODIFYING or DELETING an existing record is Quadrant D, while ADDING
a new record is the legitimate path (falls through to A/B).
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    ClassifierVote,
    PRContext,
)

CRITICAL_PREFIXES = (
    "src/",
    "schemas/",
    "docs/concepts/",
    ".github/workflows/",
    "config/",      # the gate's own config: changing it reconfigures enforcement.
    "governance/",  # the gate's own decision logic (scripts, rubric, schemas).
)

# Paths that govern enforcement itself → ALWAYS Quadrant D when MODIFIED, added,
# or removed (not merely deleted). Editing any of these reconfigures or weakens
# the gate, so it must always route to the owner — never auto-merge as A/B.
ALWAYS_D_PREFIXES = (
    "docs/concepts/",  # Tier 3 operating doctrine ("always D regardless of diff")
    "config/",         # gate configuration (required_checks, audit_only, registry)
    "governance/",     # gate decision logic (scripts, rubric, schemas)
    ".github/",        # CI runtime: workflows AND the scripts/actions they invoke
                       #   (workflows execute .github/scripts/*; both can forge a
                       #    CI signal, so the whole dir is owner-gated)
    "schemas/",        # required-field schema changes are D; conservatively all schema edits
    "bot-state/",      # Tier 5 audit & receipts (non-bot writes must be owner-gated)
)

# Tier 2 immutable records: modifying/deleting an EXISTING ADR or meeting record
# is Quadrant D (append-only); adding a NEW record is legitimate (falls through).
APPEND_ONLY_PREFIXES = ("docs/decisions/", "docs/meetings/")
_NON_ADDITIVE = ("modified", "removed", "renamed", "changed")


class PathDefaultClassifier:
    name = "classifier_path_default"

    def evaluate(self, pr_context: PRContext) -> ClassifierVote:
        files = pr_context.files_changed
        if not files:
            # Empty PRs are handled by classifier_empty_pr.
            return ClassifierVote(quadrant="A", reasoning="no file changes")

        # SELF-CONTAINMENT: a change touching the gate's own doctrine / config /
        # decision logic / CI / schemas / audit state is ALWAYS Quadrant D, even
        # for a pure modification — an agent must not be able to quietly weaken
        # the rules that constrain it (five-tier-files.md Tier 3/4/5).
        # BOTH the new path AND (for a rename) the OLD path are checked, so
        # renaming a file OUT of a governed dir (config/x.yml → docs/x.md) cannot
        # escape D by being classified on the new name alone.
        def _paths(f):
            yield f.path
            if f.previous_filename:
                yield f.previous_filename

        always_d = [
            f.path for f in files
            if any(p.startswith(ALWAYS_D_PREFIXES) for p in _paths(f))
        ]
        # Tier 2 append-only: modifying/deleting/renaming an existing record
        # (not a pure add). A rename of an existing ADR/meeting is non-additive.
        append_d = [
            f.path for f in files
            if f.status in _NON_ADDITIVE
            and any(p.startswith(APPEND_ONLY_PREFIXES) for p in _paths(f))
        ]
        if always_d or append_d:
            hit = (always_d + append_d)[0]
            return ClassifierVote(
                quadrant="D",
                reasoning=(
                    f"enforcement-governing or append-only path edited ({hit}) — "
                    "always owner-gated (Quadrant D) regardless of diff"
                ),
            )

        is_critical = any(
            f.path.startswith(CRITICAL_PREFIXES) for f in files
        )
        is_irreversible = any(f.status == "removed" for f in files)

        if is_irreversible and is_critical:
            return ClassifierVote(
                quadrant="D",
                reasoning=(
                    "irreversible + critical (deletes a file in src/, schemas/, "
                    "docs/concepts/, .github/workflows/, config/, or governance/)"
                ),
            )
        if is_critical:
            return ClassifierVote(
                quadrant="B",
                reasoning="reversible + critical (modifies a critical path)",
            )
        if is_irreversible:
            return ClassifierVote(
                quadrant="C",
                reasoning="irreversible + non-critical (deletes a non-critical file)",
            )
        return ClassifierVote(
            quadrant="A",
            reasoning="reversible + non-critical",
        )
