"""Boundary and compatibility tests for the canonical trailer contract."""

from __future__ import annotations

import pytest

from multiagent_protocol.trailer_contract import (
    AGENT_SESSION_PATTERN,
    TASK_REF_PATTERN,
)


@pytest.mark.parametrize(
    ("value", "accepted"),
    (
        ("s_abc", False),  # 3 characters after s_
        ("s_abcd", True),  # documented minimum: 4
        ("s_a-bc", True),
        ("s_abcdefghijklmnop", True),  # documented maximum: 16
        ("s_abcdefghijklmnopq", False),
        ("s_-abc", False),
        ("s_abc-", False),
        ("s_Abcd", False),
    ),
)
def test_agent_session_contract(value: str, accepted: bool):
    assert (AGENT_SESSION_PATTERN.fullmatch(value) is not None) is accepted


@pytest.mark.parametrize(
    ("value", "accepted"),
    (
        ("Issue#1", True),
        ("issue#1", True),  # historical owner-delta spelling
        ("ISSUE#1", False),
        ("PR#1", True),
        ("pr#1", False),
        ("none", True),
        ("round-7/parser-parity", True),
        ("bot/auto_revert.v1", True),
        ("Issue#0", True),
        ("Issue#", False),
        ("round-7/", False),
        ("issue#1/extra", False),
    ),
)
def test_task_ref_contract(value: str, accepted: bool):
    assert (TASK_REF_PATTERN.fullmatch(value) is not None) is accepted
