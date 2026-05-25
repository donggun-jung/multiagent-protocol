"""Tests for hook_hallucination_guard.

The hallucination guard is general-preferences.md § 1 — the protocol's
top-listed built-in. It is meant to catch the common AI-failure mode
where a commit body references a file that does not exist at the merged
SHA. Before R3 there were zero tests on this hook.
"""

from __future__ import annotations

from multiagent_protocol.skills.builtin.hook_hallucination_guard import (
    HallucinationGuardHook,
)
from multiagent_protocol.types import CommitContext, TrailerSet


def _commit(body: str, sha: str = "a" * 40) -> CommitContext:
    return CommitContext(
        sha=sha,
        subject="test commit",
        body=body,
        author_login="alice",
        committer_login="alice",
        parents=(),
        trailers=TrailerSet(),
    )


def test_hook_passes_when_no_file_references():
    """Body with no `path-like-string` references → no incident."""
    hook = HallucinationGuardHook(repo_path_resolver=lambda p, sha: False)
    r = hook.on_commit(_commit("This is just prose, no backticks at all."))
    assert r.incident_label is None


def test_hook_passes_when_no_resolver_configured():
    """Without a resolver injected, the hook is best-effort and passes."""
    hook = HallucinationGuardHook(repo_path_resolver=None)
    r = hook.on_commit(_commit("References `src/foo.py` here."))
    assert r.incident_label is None


def test_hook_passes_when_referenced_path_exists():
    hook = HallucinationGuardHook(repo_path_resolver=lambda p, sha: True)
    r = hook.on_commit(_commit(
        "See `src/multiagent_protocol/main.py` for the entry point."
    ))
    assert r.incident_label is None


def test_hook_flags_missing_path():
    hook = HallucinationGuardHook(repo_path_resolver=lambda p, sha: False)
    r = hook.on_commit(_commit(
        "Fix referenced in `src/auth/legacy.py` — see line 42."
    ))
    assert r.incident_label == "decision:hallucination-detected"
    assert "src/auth/legacy.py" in r.incident_body


def test_hook_flags_only_truly_missing_paths():
    """Resolver returns True for one path, False for another → only the
    missing one is reported."""
    def resolver(path: str, sha: str) -> bool:
        return path == "src/multiagent_protocol/main.py"

    hook = HallucinationGuardHook(repo_path_resolver=resolver)
    r = hook.on_commit(_commit(
        "See `src/multiagent_protocol/main.py` for the real one and "
        "`src/multiagent_protocol/imaginary.py` for the fake."
    ))
    assert r.incident_label == "decision:hallucination-detected"
    assert "src/multiagent_protocol/imaginary.py" in r.incident_body
    # The real one must NOT appear in the missing list.
    assert "src/multiagent_protocol/main.py" not in r.incident_body


def test_hook_deduplicates_repeated_references():
    """If the body mentions the same missing path 5 times, it appears once
    in the incident body."""
    hook = HallucinationGuardHook(repo_path_resolver=lambda p, sha: False)
    body = (
        "First `src/foo.py` then `src/foo.py` and then again `src/foo.py`. "
        "Maybe `src/foo.py` once more for good measure: `src/foo.py`."
    )
    r = hook.on_commit(_commit(body))
    assert r.incident_label == "decision:hallucination-detected"
    # `src/foo.py` should appear exactly once in the missing-paths list.
    assert r.incident_body.count("`src/foo.py`") == 1


def test_hook_ignores_non_path_backticked_strings():
    """`agent_session`, `app.slug`, `bytes`, etc. should not match the
    file-path heuristic (they have no /, no extension, or extension too
    long).
    """
    hook = HallucinationGuardHook(repo_path_resolver=lambda p, sha: False)
    body = (
        "Discusses `agent_session`, the `app.slug` field, and the `dict` "
        "type. Also `application/json`. None of these are paths."
    )
    r = hook.on_commit(_commit(body))
    assert r.incident_label is None


def test_hook_handles_resolver_exception_gracefully():
    """If the resolver raises (network blip, rate limit), skip the path
    rather than failing the whole hook."""
    def flaky(path: str, sha: str) -> bool:
        raise RuntimeError("network blip")

    hook = HallucinationGuardHook(repo_path_resolver=flaky)
    r = hook.on_commit(_commit("Mentions `src/foo.py` once."))
    # The flaky resolver should not crash the hook; in this case the
    # commit "passes" because the hook could not verify.
    assert r.incident_label is None


def test_hook_handles_empty_body():
    hook = HallucinationGuardHook(repo_path_resolver=lambda p, sha: False)
    r = hook.on_commit(_commit(""))
    assert r.incident_label is None
