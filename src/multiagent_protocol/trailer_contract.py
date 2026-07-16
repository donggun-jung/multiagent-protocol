"""Canonical value contract for agent identity trailers.

Keep value-shape rules here so every engine consumer evaluates the same
``Agent-Session`` and ``Task-Ref`` language.  Commit-message block parsing
remains in :mod:`multiagent_protocol.trailers`.
"""

from __future__ import annotations

import re

# ``s_`` followed by 4-16 lowercase alphanumeric/hyphen characters.  The
# first and last characters after ``s_`` are alphanumeric, preventing IDs
# that begin or end with a separator.
AGENT_SESSION_PATTERN_TEXT = r"^s_[a-z0-9][a-z0-9-]{2,14}[a-z0-9]$"

# Both Issue#N (the public-engine spelling) and issue#N (the historical
# owner-delta spelling) remain valid.  New examples use Issue#N, but accepting
# both prevents existing provenance from becoming invalid after convergence.
TASK_REF_PATTERN_TEXT = (
    r"^(?:[Ii]ssue#\d+|PR#\d+|none|round-\d+/[A-Za-z0-9/_.-]+|"
    r"bot/[A-Za-z0-9/_.-]+)$"
)

AGENT_SESSION_PATTERN = re.compile(AGENT_SESSION_PATTERN_TEXT)
TASK_REF_PATTERN = re.compile(TASK_REF_PATTERN_TEXT)
