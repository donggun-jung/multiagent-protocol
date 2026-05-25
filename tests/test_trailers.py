"""Tests for trailer parsing."""

from __future__ import annotations

from multiagent_protocol.trailers import parse_trailers


def test_parse_complete_trailer_block():
    msg = """Subject line

Body paragraph here.

Agent-Tool: claude-code
Agent-Model: claude-opus-4.7
Agent-Session: s_test123
Agent-Machine: ci-runner
Task-Ref: PR#42
"""
    t = parse_trailers(msg)
    assert t.agent_tool == "claude-code"
    assert t.agent_model == "claude-opus-4.7"
    assert t.agent_session == "s_test123"
    assert t.agent_machine == "ci-runner"
    assert t.task_ref == "PR#42"
    assert t.is_complete()


def test_parse_missing_trailers_returns_partial_set():
    msg = """Subject

Body.

Agent-Tool: cursor
Task-Ref: none
"""
    t = parse_trailers(msg)
    assert t.agent_tool == "cursor"
    assert t.task_ref == "none"
    assert t.agent_model is None
    assert not t.is_complete()


def test_no_trailers_at_all():
    msg = "Subject\n\nJust a body, no trailers.\n"
    t = parse_trailers(msg)
    assert t.is_complete() is False
    assert t.agent_tool is None
    assert t.raw == {}


def test_mid_prose_trailer_like_line_is_not_a_trailer():
    msg = """Subject

I mention Agent-Tool: claude-code in the middle of prose.

Then this is the real body.
"""
    t = parse_trailers(msg)
    # No trailing trailer block → no extraction.
    assert t.agent_tool is None
    assert t.raw == {}


def test_trailer_block_with_unknown_keys_preserved_in_raw():
    msg = """Subject

Body.

Agent-Tool: claude-code
Co-Authored-By: human <h@example.com>
Reviewed-By: someone@example.com
Agent-Session: s_abc1234
Agent-Model: m1
Agent-Machine: m
Task-Ref: PR#1
"""
    t = parse_trailers(msg)
    assert t.agent_tool == "claude-code"
    assert "Co-Authored-By" in t.raw
    assert "Reviewed-By" in t.raw
    assert t.is_complete()


def test_empty_message():
    t = parse_trailers("")
    assert t.agent_tool is None
    assert not t.is_complete()


def test_trailer_line_with_extra_whitespace():
    msg = """S

B

Agent-Tool:   claude-code
Agent-Model:claude-opus-4.7
Agent-Session: s_ok
Agent-Machine: m
Task-Ref: none
"""
    t = parse_trailers(msg)
    assert t.agent_tool == "claude-code"
    assert t.agent_model == "claude-opus-4.7"  # leading space removed
    assert t.agent_session == "s_ok"
