"""FEATURE A — L2 automatic revert-PR (auto_revert.ensure_revert_pr).

All git work goes through an injected ``runner`` so no real git runs. Covers:
- happy path: clone → revert → amend trailers → push → PR opened;
- parent inspection: one parent proceeds; two/three parents fail closed before
  ``git revert``; an inspection failure also fails closed;
- the amended commit carries the five Agent-* trailers (gate-evaluable);
- revert-conflict → graceful fallback (no PR, incident-only note, abort called);
- duplicate branch + open PR → idempotent link (no clone, no new PR);
- branch exists but no open PR → open PR against the existing branch, no clone;
- clone / push failure → graceful fallback;
- no installation token → graceful skip;
- token redaction in the failure note.
"""

from __future__ import annotations

import pytest

from multiagent_protocol.auto_revert import (
    REVERT_AGENT_MODEL,
    REVERT_AGENT_SESSION,
    REVERT_AGENT_TOOL,
    RunResult,
    ensure_revert_pr,
    revert_branch_name,
)
from tests.conftest import FakeAPI

BAD = "dead" + "b" * 36  # 40-char sha, sha7 = "deadbbb"
# A clearly-fake installation token (deliberately NOT gh*_-prefixed, so the
# personal-data scan never mistakes this fixture for a real credential).
TOKEN = "fake-install-token-xyz"


class _Runner:
    """Records git argv lists and returns scripted RunResults.

    ``script`` maps a matched substring of the joined argv to a RunResult; the
    default is success. A ``log`` command returns a canned revert message so the
    amend step has something to append to."""

    def __init__(
        self,
        script: dict[str, RunResult] | None = None,
        log_msg: str = "Revert \"bad thing\"\n\nThis reverts commit deadbbb.",
        parents: tuple[str, ...] = ("1" * 40,),
    ) -> None:
        self.script = script or {}
        self.log_msg = log_msg
        self.parents = parents
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, cwd=None):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for needle, result in self.script.items():
            if needle in joined:
                return result
        if argv[:3] == ["git", "cat-file", "commit"]:
            parent_headers = "".join(f"parent {sha}\n" for sha in self.parents)
            return RunResult(
                0,
                stdout=(
                    f"tree {'f' * 40}\n{parent_headers}"
                    "author Example Contributor <you@example.com> 0 +0000\n"
                    "committer Example Contributor <you@example.com> 0 +0000\n\n"
                    "bad commit\n"
                ),
            )
        if argv[:3] == ["git", "log", "-1"]:
            return RunResult(0, stdout=self.log_msg)
        return RunResult(0)

    def ran(self, needle: str) -> bool:
        return any(needle in " ".join(c) for c in self.calls)


def _api() -> FakeAPI:
    return FakeAPI(main_head="h" * 40)


# -- happy path ----------------------------------------------------------------

def test_happy_path_opens_pr_and_pushes_branch():
    api = _api()
    runner = _Runner()
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is not None and res.created is True
    assert "Auto-revert PR opened" in res.note
    # A PR was created from the deterministic revert branch into main.
    assert len(api.prs_created) == 1
    pr = api.prs_created[0]
    assert pr["head"]["ref"] == revert_branch_name(BAD) == "revert/deadbbb"
    assert pr["base"]["ref"] == "main"
    # The git flow ran: clone (depth+branch), revert --no-edit, amend, push.
    assert runner.ran("git clone --depth 50 --branch main")
    assert runner.ran(f"git revert --no-edit {BAD}")
    assert runner.ran("git commit --amend")
    assert runner.ran("push origin HEAD:refs/heads/revert/deadbbb")


def test_single_parent_target_keeps_normal_revert_path():
    api = _api()
    runner = _Runner(parents=("1" * 40,))
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is not None and res.created is True
    assert runner.ran("git cat-file commit")
    assert runner.ran(f"git revert --no-edit {BAD}")


@pytest.mark.parametrize("parent_count", [2, 3], ids=["two-parent", "three-parent"])
def test_multi_parent_target_fails_closed_before_git_revert(parent_count):
    api = _api()
    parents = tuple(str(i) * 40 for i in range(1, parent_count + 1))
    runner = _Runner(parents=parents)
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is None and res.created is False
    assert "multi-parent-merge" in res.note
    assert "git show --format=%P" in res.note
    assert "git revert -m N" in res.note
    assert not any(call[:2] == ["git", "revert"] for call in runner.calls)
    assert api.prs_created == []


def test_parent_inspection_failure_fails_closed_before_git_revert():
    api = _api()
    runner = _Runner(script={
        "git cat-file": RunResult(128, stderr="fatal: bad object"),
    })
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is None and res.created is False
    assert "parent-inspection" in res.note
    assert not any(call[:2] == ["git", "revert"] for call in runner.calls)
    assert api.prs_created == []


def test_amended_commit_carries_all_identity_trailers():
    api = _api()
    captured: dict[str, str] = {}

    class _Capture(_Runner):
        def __call__(self, argv, *, cwd=None):
            if argv[:3] == ["git", "commit", "--amend"]:
                # argv = [git, commit, --amend, -m, <message>]
                captured["msg"] = argv[argv.index("-m") + 1]
            return super().__call__(argv, cwd=cwd)

    ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42",
        runner=_Capture(),
    )
    msg = captured["msg"]
    assert f"Agent-Tool: {REVERT_AGENT_TOOL}" in msg
    assert f"Agent-Model: {REVERT_AGENT_MODEL}" in msg
    assert f"Agent-Session: {REVERT_AGENT_SESSION}" in msg
    assert "Agent-Machine: bot" in msg
    assert "Task-Ref: Issue#42" in msg  # the incident issue ref
    # The original revert message is preserved above the trailers.
    assert "This reverts commit" in msg


# -- revert conflict → graceful fallback --------------------------------------

def test_revert_conflict_falls_back_and_aborts():
    api = _api()
    runner = _Runner(script={
        f"revert --no-edit {BAD}": RunResult(1, stderr="error: could not revert deadbbb\nhint: conflicts"),
    })
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is None and res.created is False
    assert "not** created" in res.note and "revert-conflict" in res.note
    assert api.prs_created == []            # no PR opened
    assert runner.ran("git revert --abort")  # working tree cleaned up
    assert not runner.ran("push origin")     # nothing pushed


# -- idempotency ---------------------------------------------------------------

def test_existing_branch_and_open_pr_is_linked_not_duplicated():
    api = _api()
    branch = revert_branch_name(BAD)
    api._refs[("acme", "app", branch)] = "existing" + "0" * 32
    existing = api.seed_open_pr_for_head("acme", "app", branch,
                                         html_url="https://github.com/acme/app/pull/9")
    runner = _Runner()
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url == existing["html_url"]
    assert res.created is False and "already open" in res.note
    assert api.prs_created == []      # not duplicated
    assert runner.calls == []         # no git ran at all (pure idempotent link)


def test_existing_branch_without_pr_opens_pr_without_recloning():
    api = _api()
    branch = revert_branch_name(BAD)
    api._refs[("acme", "app", branch)] = "existing" + "0" * 32   # branch pushed…
    # …but no open PR from it (previous tick died between push and PR-open).
    runner = _Runner()
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is not None and res.created is True
    assert len(api.prs_created) == 1
    assert runner.calls == []   # reused the existing branch — no clone/revert/push


# -- push / clone failure → graceful fallback ---------------------------------

def test_clone_failure_falls_back():
    api = _api()
    runner = _Runner(script={"git clone": RunResult(128, stderr="fatal: repository not found")})
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is None and "clone" in res.note
    assert api.prs_created == []
    assert not runner.ran("git revert")


def test_push_failure_falls_back():
    api = _api()
    runner = _Runner(script={"push origin": RunResult(1, stderr="! [remote rejected]")})
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is None and "push" in res.note
    assert api.prs_created == []


def test_pr_open_failure_after_push_is_graceful():
    class _RaisePR(FakeAPI):
        def create_pull_request(self, *a, **k):
            raise RuntimeError("422 validation failed")

    api = _RaisePR(main_head="h" * 40)
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=_Runner(),
    )
    assert res.pr_url is None and res.created is False
    assert "was pushed" in res.note and "manually" in res.note


# -- degradation & security ----------------------------------------------------

def test_no_token_degrades_to_incident_only():
    api = _api()
    runner = _Runner()
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=None, incident_ref="Issue#42", runner=runner,
    )
    assert res.pr_url is None and res.created is False
    assert "no installation token" in res.note
    assert runner.calls == []          # never attempted to clone without a token
    assert api.prs_created == []


def test_token_is_redacted_from_failure_note():
    api = _api()
    runner = _Runner(script={
        "git clone": RunResult(128, stderr=f"fatal: could not read from https://x-access-token:{TOKEN}@github.com/acme/app.git"),
    })
    res = ensure_revert_pr(
        api, "acme", "app", BAD, token=TOKEN, incident_ref="Issue#42", runner=runner,
    )
    assert TOKEN not in res.note        # the secret never leaks into the issue body
    assert "x-access-token:***" in res.note
