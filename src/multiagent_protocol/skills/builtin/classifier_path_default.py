"""Default path-based classifier rule.

Reads ``config/classifier_rules.yml`` if present; otherwise applies a
conservative built-in heuristic:

- Any change touching ``src/`` or ``schemas/`` or ``docs/concepts/`` or
  ``.github/workflows/`` is **critical**.
- Any change deleting files is **irreversible**.
- The cross-product yields A/B/C/D as in ``docs/concepts/four-quadrants.md``.
"""

from __future__ import annotations

from multiagent_protocol.skills.base import (
    ClassifierRule,
    ClassifierVote,
    PRContext,
)


CRITICAL_PREFIXES = (
    "src/",
    "schemas/",
    "docs/concepts/",
    ".github/workflows/",
)


class PathDefaultClassifier:
    name = "classifier_path_default"

    def evaluate(self, pr_context: PRContext) -> ClassifierVote:
        files = pr_context.files_changed
        if not files:
            # Empty PRs are handled by classifier_empty_pr.
            return ClassifierVote(quadrant="A", reasoning="no file changes")

        is_critical = any(
            f.path.startswith(CRITICAL_PREFIXES) for f in files
        )
        is_irreversible = any(f.status == "removed" for f in files)

        if is_irreversible and is_critical:
            return ClassifierVote(
                quadrant="D",
                reasoning=(
                    "irreversible + critical (deletes a file in src/, schemas/, "
                    "docs/concepts/, or .github/workflows/)"
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
