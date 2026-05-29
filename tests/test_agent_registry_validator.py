"""L4 identity gate — validator_agent_registry (advisory P2)."""

from __future__ import annotations

from multiagent_protocol.config.loader import AgentRegistry
from multiagent_protocol.skills.builtin.validator_agent_registry import (
    AgentRegistryValidator,
)
from multiagent_protocol.types import TrailerSet


def _pr(pr_factory, commit_factory, tool, model):
    t = TrailerSet(agent_tool=tool, agent_model=model, agent_session="s_x1",
                   agent_machine="m", task_ref="none")
    return pr_factory(commits=(commit_factory(trailers=t),))


def test_no_registry_is_noop(pr_factory, commit_factory):
    pr = _pr(pr_factory, commit_factory, "anything", "any")
    assert AgentRegistryValidator(registry=None).check(pr).passed


def test_registered_tool_model_passes(pr_factory, commit_factory):
    reg = AgentRegistry(tools=("claude-code",), models={"claude-code": ("*",)})
    pr = _pr(pr_factory, commit_factory, "claude-code", "claude-opus-4.8")
    assert AgentRegistryValidator(registry=reg).check(pr).passed


def test_unregistered_tool_fails(pr_factory, commit_factory):
    reg = AgentRegistry(tools=("claude-code",), models={"claude-code": ("*",)})
    pr = _pr(pr_factory, commit_factory, "rogue-agent", "x")
    r = AgentRegistryValidator(registry=reg).check(pr)
    assert not r.passed
    assert "L4" in r.failure_reason


def test_model_not_allowed_fails(pr_factory, commit_factory):
    reg = AgentRegistry(tools=("manual",), models={"manual": ("n/a",)})
    pr = _pr(pr_factory, commit_factory, "manual", "gpt-5")
    assert not AgentRegistryValidator(registry=reg).check(pr).passed


def test_default_severity_is_advisory():
    # Ships advisory; operators promote to P0 via severity_overrides.
    assert AgentRegistryValidator().severity == "P2"
