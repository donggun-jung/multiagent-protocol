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
