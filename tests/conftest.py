"""Shared pytest fixtures for the test suite.

Builds in-memory PRContext / CommitContext objects so tests do not need a
GitHub API client.
"""

from __future__ import annotations

import pytest

from multiagent_protocol.trailers import parse_trailers
from multiagent_protocol.types import (
    CheckRunStatus,
    CommitContext,
    FileChange,
    LabelEvent,
    PRContext,
    TrailerSet,
)


def make_commit(
    sha: str = "a" * 40,
    subject: str = "feat: thing",
    body: str = "",
    author_login: str = "alice",
    committer_login: str | None = None,
    parents: tuple[str, ...] = ("p" * 40,),
    trailers: TrailerSet | None = None,
    full_message: str | None = None,
) -> CommitContext:
    """Build a CommitContext for tests.

    If ``trailers`` is None and ``full_message`` is provided, trailers are
    parsed from the full message. If both are None, an empty TrailerSet is
    used. If ``full_message`` is provided, ``subject`` and ``body`` are
    overridden.
    """
    if full_message is not None:
        lines = full_message.split("\n", 1)
        subject = lines[0]
        body = lines[1] if len(lines) > 1 else ""
        if trailers is None:
            trailers = parse_trailers(full_message)
    if trailers is None:
        trailers = TrailerSet()
    return CommitContext(
        sha=sha,
        subject=subject,
        body=body,
        author_login=author_login,
        committer_login=committer_login or author_login,
        parents=parents,
        trailers=trailers,
    )


def make_pr_context(
    number: int = 100,
    labels: tuple[str, ...] = (),
    commits: tuple[CommitContext, ...] = (),
    files_changed: tuple[FileChange, ...] = (),
    check_runs: tuple[CheckRunStatus, ...] = (),
    label_events: tuple[LabelEvent, ...] = (),
    head_sha: str = "h" * 40,
    base_sha: str = "b" * 40,
    author_login: str = "alice",
    title: str = "test PR",
    body: str = "",
) -> PRContext:
    return PRContext(
        repo_owner="example",
        repo_name="repo",
        number=number,
        title=title,
        body=body,
        head_sha=head_sha,
        base_sha=base_sha,
        base_ref="main",
        state="open",
        merged=False,
        labels=labels,
        author_login=author_login,
        commits=commits,
        files_changed=files_changed,
        check_runs=check_runs,
        label_events=label_events,
    )


@pytest.fixture
def commit_factory():
    return make_commit


@pytest.fixture
def pr_factory():
    return make_pr_context


# ---------------------------------------------------------------------------
# Fake GitHub API for orchestrator / runtime tests.
#
# Records side effects (merges, comments, issues, labels) and serves canned
# PR / commit / file / check / issue data. Matches the subset of the real
# GitHubAPI surface that the bot calls.
# ---------------------------------------------------------------------------


def green_check(name: str = "ci", slug: str = "github-actions") -> dict:
    return {
        "name": name, "status": "completed", "conclusion": "success",
        "started_at": "2026-05-25T00:00:00Z", "completed_at": "2026-05-25T00:01:00Z",
        "app": {"slug": slug}, "output": {"summary": ""},
    }


def make_check(name: str, conclusion: str, *, status: str = "completed",
               started_at: str = "2026-05-25T00:00:00Z",
               completed_at: str = "2026-05-25T00:01:00Z", slug: str = "github-actions") -> dict:
    return {
        "name": name, "status": status, "conclusion": conclusion,
        "started_at": started_at, "completed_at": completed_at,
        "app": {"slug": slug}, "output": {"summary": ""},
    }


def changed_file(path: str, status: str = "modified") -> dict:
    return {"filename": path, "status": status, "additions": 1, "deletions": 0}


WELL_FORMED_TRAILERS = (
    "Agent-Tool: claude-code\n"
    "Agent-Model: claude-opus-4.7\n"
    "Agent-Session: s_test123\n"
    "Agent-Machine: ci\n"
    "Task-Ref: PR#1"
)


def raw_commit(sha: str = "c" * 40, subject: str = "feat: thing",
               body: str = "body", trailers: str = WELL_FORMED_TRAILERS,
               author: str = "alice", date: str = "2026-05-25T00:00:00Z") -> dict:
    message = f"{subject}\n\n{body}\n\n{trailers}" if trailers else f"{subject}\n\n{body}"
    return {
        "sha": sha,
        "commit": {"message": message, "committer": {"date": date}},
        "author": {"login": author}, "committer": {"login": author},
        "parents": [{"sha": "p" * 40}],
    }


class FakeAPI:
    """In-memory stand-in for :class:`multiagent_protocol.github_api.GitHubAPI`."""

    def __init__(self, main_head: str = "m" * 40) -> None:
        self._main_head = main_head
        self._prs: dict[tuple[str, str], list[dict]] = {}
        self._commits: dict[int, list[dict]] = {}
        self._files: dict[int, list[dict]] = {}
        self._checks: dict[str, list[dict]] = {}
        self._label_events: dict[int, list[dict]] = {}
        self._main_commits: dict[tuple[str, str], list[dict]] = {}
        self._issues: list[dict] = []
        self._reactions: dict[int, list[dict]] = {}
        self._issue_comments: dict[int, list[dict]] = {}
        self._dir: dict[tuple[str, str, str], list[dict]] = {}
        self._files_text: dict[tuple[str, str, str], str] = {}
        self._existing_paths: set[tuple[str, str]] = set()  # (path, sha) that exist
        self._issue_counter = 1000
        # side-effect records
        self.merged: list[tuple] = []
        self.comments_posted: list[tuple] = []
        self.issues_opened: list[dict] = []
        self.labels_added: list[tuple] = []
        self.closed: list[tuple] = []
        self.updated_branches: list[tuple] = []

    # -- seeding --
    def register_pr(self, *, owner="example", repo="repo", number=1, labels=(),
                    files=(), checks=None, commits=None, head_sha=None,
                    base_sha=None, author="alice", title="t", body="",
                    label_actor="your-github-login", label_events=None) -> dict:
        head_sha = head_sha or ("h" * 40)
        base_sha = base_sha if base_sha is not None else self._main_head
        payload = {
            "number": number, "title": title, "body": body,
            "base": {"repo": {"owner": {"login": owner}, "name": repo},
                     "sha": base_sha, "ref": "main"},
            "head": {"sha": head_sha}, "state": "open", "merged": False,
            "labels": [{"name": n} for n in labels],
            "user": {"login": author},
        }
        self._prs.setdefault((owner, repo), []).append(payload)
        self._commits[number] = commits if commits is not None else [raw_commit()]
        self._files[number] = list(files)
        self._checks[head_sha] = list(checks) if checks is not None else [green_check()]
        # By default, each label was applied by an allowlisted actor (so C1's
        # actor check passes); override via label_events for negative tests.
        if label_events is not None:
            self._label_events[number] = list(label_events)
        else:
            self._label_events[number] = [
                {"label": n, "actor": label_actor, "created_at": "2026-05-25T00:00:00Z"}
                for n in labels
            ]
        return payload

    def seed_issue(self, *, number=None, labels=(), body="", title="") -> dict:
        if number is None:
            self._issue_counter += 1
            number = self._issue_counter
        issue = {"number": number, "title": title, "body": body,
                 "labels": [{"name": n} for n in labels], "_labels": set(labels)}
        self._issues.append(issue)
        return issue

    def seed_reaction(self, issue_number, login, content, created_at="2026-05-25T00:00:00Z"):
        self._reactions.setdefault(issue_number, []).append(
            {"user": {"login": login}, "content": content, "created_at": created_at})

    def seed_comment(self, issue_number, login, body, created_at="2026-05-25T00:00:00Z"):
        self._issue_comments.setdefault(issue_number, []).append(
            {"user": {"login": login}, "body": body, "created_at": created_at})

    def seed_main_commits(self, owner, repo, commits):
        self._main_commits[(owner, repo)] = commits

    # -- read surface --
    def list_open_prs(self, owner, repo): return list(self._prs.get((owner, repo), []))
    def pr(self, owner, repo, number):
        for prs in self._prs.values():
            for p in prs:
                if p["number"] == number:
                    return p
        return {"head": {"sha": "?"}}
    def pr_commits(self, owner, repo, number): return self._commits.get(number, [])
    def pr_files(self, owner, repo, number): return self._files.get(number, [])
    def check_runs(self, owner, repo, sha): return self._checks.get(sha, [])
    def label_events(self, owner, repo, number): return self._label_events.get(number, [])
    def main_head_sha(self, owner, repo): return self._main_head
    def list_commits_on_main(self, owner, repo, since_sha=None):
        commits = self._main_commits.get((owner, repo), [])
        if since_sha is None:
            return list(commits)
        out = []
        for c in commits:
            if c["sha"] == since_sha:
                break
            out.append(c)
        return out
    def list_issues(self, owner, repo, *, labels=None, state="open"):
        if labels is None:
            return list(self._issues)
        return [i for i in self._issues if labels in i.get("_labels", set())]
    def list_issue_reactions(self, owner, repo, number): return self._reactions.get(number, [])
    def list_issue_comments(self, owner, repo, number): return self._issue_comments.get(number, [])
    def list_dir(self, owner, repo, path, ref="main"): return self._dir.get((owner, repo, path), [])
    def get_file_text(self, owner, repo, path, ref="main"): return self._files_text.get((owner, repo, path))
    def file_exists_at_sha(self, owner, repo, path, sha): return (path, sha) in self._existing_paths or not self._existing_paths

    # -- write surface (recorded) --
    def merge_pr(self, owner, repo, number, *, head_sha, method="squash", **kw):
        self.merged.append((owner, repo, number, head_sha))
        return {"merged": True}
    def update_branch(self, owner, repo, number):
        self.updated_branches.append((owner, repo, number))
        return True
    def post_comment(self, owner, repo, number, body):
        self.comments_posted.append((owner, repo, number, body))
    def add_label(self, owner, repo, number, label):
        self.labels_added.append((owner, repo, number, label))
    def remove_label(self, owner, repo, number, label): pass
    def open_issue(self, *, owner, repo, title, body, labels=None):
        self._issue_counter += 1
        issue = {"number": self._issue_counter, "title": title, "body": body,
                 "labels": [{"name": n} for n in (labels or [])], "_labels": set(labels or [])}
        self.issues_opened.append(issue)
        self._issues.append(issue)  # so dedupe sees it within the same tick
        return issue
    def close_issue(self, owner, repo, number):
        self.closed.append((owner, repo, number))


@pytest.fixture
def fake_api():
    return FakeAPI()


@pytest.fixture
def solo_config():
    from pathlib import Path

    from multiagent_protocol.config.loader import load_config
    root = Path(__file__).resolve().parents[1]
    return load_config(root / "examples/solo-developer/config", root / "schemas")
