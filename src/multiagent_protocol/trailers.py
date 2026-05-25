"""Commit message trailer parsing.

Implements the same trailer semantics as ``git interpret-trailers``: any
key-value line in the form ``<Token>: <value>`` at the **end** of a commit
message body, contiguous to other trailers, is a trailer.

We do not invoke ``git`` because the bot operates on commit JSON from the
GitHub API, not the local working tree. We re-implement the relevant subset.
"""

from __future__ import annotations

from multiagent_protocol.types import TrailerSet

# Known trailer keys we extract into TrailerSet fields. Unknown keys go into
# ``raw`` for completeness but are not validated.
_KNOWN = {
    "Agent-Tool": "agent_tool",
    "Agent-Model": "agent_model",
    "Agent-Session": "agent_session",
    "Agent-Machine": "agent_machine",
    "Task-Ref": "task_ref",
}


def parse_trailers(commit_message: str) -> TrailerSet:
    """Parse trailers from a complete commit message (subject + body).

    Trailers are key-value pairs at the very end of the body, separated
    from prose by a blank line. A trailer line looks like ``Key: value``
    where ``Key`` matches ``^[A-Za-z][A-Za-z0-9-]*$``.

    Unknown keys are preserved in ``TrailerSet.raw`` but do not populate
    typed fields.
    """
    if not commit_message:
        return TrailerSet()

    lines = commit_message.rstrip("\n").split("\n")
    # Walk backwards to find the trailing block of trailers.
    trailer_lines: list[str] = []
    for line in reversed(lines):
        stripped = line.rstrip()
        if stripped == "":
            # Blank line above the trailer block ends our scan.
            break
        # A trailer line must have ``Key: value`` form.
        if not _is_trailer_line(stripped):
            # Mid-prose line in what we thought was the trailer block;
            # there is no trailer block.
            return _empty_trailers_with_raw_if_any(commit_message)
        trailer_lines.insert(0, stripped)

    raw: dict[str, str] = {}
    extracted: dict[str, str] = {}
    for line in trailer_lines:
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        raw[key] = value
        if key in _KNOWN:
            extracted[_KNOWN[key]] = value

    return TrailerSet(
        agent_tool=extracted.get("agent_tool"),
        agent_model=extracted.get("agent_model"),
        agent_session=extracted.get("agent_session"),
        agent_machine=extracted.get("agent_machine"),
        task_ref=extracted.get("task_ref"),
        raw=raw,
    )


def _is_trailer_line(line: str) -> bool:
    if ":" not in line:
        return False
    key, _, _ = line.partition(":")
    key = key.strip()
    if not key:
        return False
    if not (key[0].isalpha()):
        return False
    return all(c.isalnum() or c == "-" for c in key)


def _empty_trailers_with_raw_if_any(commit_message: str) -> TrailerSet:
    # Even if there is no clean trailing trailer block, the body MAY contain
    # a stray "Agent-Tool: foo" line somewhere mid-prose. We do NOT extract
    # those — trailer semantics require the trailing block. This function
    # exists to make the intent obvious: malformed → empty.
    _ = commit_message
    return TrailerSet()
