"""Core data classes used across the bot.

These are read-only views passed to skills (Validator / ClassifierRule /
BranchHook). Skills cannot mutate them or make GitHub API calls; if a skill
needs information not exposed here, the protocol grows the context — not the
skill's privileges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Quadrant = Literal["A", "B", "C", "D"]
Severity = Literal["P0", "P1", "P2", "P3"]


@dataclass(frozen=True)
class TrailerSet:
    """Parsed ``Agent-*`` and ``Task-Ref`` trailers from a single commit."""

    agent_tool: str | None = None
    agent_model: str | None = None
    agent_session: str | None = None
    agent_machine: str | None = None
    task_ref: str | None = None
    raw: dict[str, str] = field(default_factory=dict)

    def is_complete(self) -> bool:
        return all(
            v is not None and v != ""
            for v in (
                self.agent_tool,
                self.agent_model,
                self.agent_session,
                self.agent_machine,
                self.task_ref,
            )
        )


@dataclass(frozen=True)
class CommitContext:
    """A commit on a PR or on ``main``.

    Skills receive this in :class:`BranchHook.on_commit` and as part of
    :class:`PRContext.commits`.
    """

    sha: str
    subject: str
    body: str
    author_login: str | None
    committer_login: str | None
    parents: tuple[str, ...]
    trailers: TrailerSet

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


@dataclass(frozen=True)
class FileChange:
    """A single file diff entry on a PR."""

    path: str
    status: Literal["added", "modified", "removed", "renamed"]
    additions: int
    deletions: int


@dataclass(frozen=True)
class CheckRunStatus:
    """A required check-run on the PR head."""

    name: str
    status: Literal["queued", "in_progress", "completed"]
    conclusion: Literal[
        "success", "failure", "neutral", "cancelled", "skipped",
        "timed_out", "action_required", "stale", "startup_failure", "",
    ] | None
    started_at: str | None
    completed_at: str | None
    app_slug: str | None
    output_summary: str | None


@dataclass(frozen=True)
class LabelEvent:
    """A label-add event on the PR."""

    label: str
    actor_login: str
    created_at: str


@dataclass(frozen=True)
class PRContext:
    """Everything a skill should know about a PR.

    This is the only object skills receive about the PR; they cannot reach
    through it to make API calls. To add a new field, edit this class and
    populate it in ``pr_validator.build_context``.
    """

    repo_owner: str
    repo_name: str
    number: int
    title: str
    body: str
    head_sha: str
    base_sha: str
    base_ref: str
    state: Literal["open", "closed"]
    merged: bool
    labels: tuple[str, ...]
    author_login: str | None
    commits: tuple[CommitContext, ...]
    files_changed: tuple[FileChange, ...]
    check_runs: tuple[CheckRunStatus, ...]
    label_events: tuple[LabelEvent, ...]

    @property
    def full_name(self) -> str:
        return f"{self.repo_owner}/{self.repo_name}"

    @property
    def commit_count(self) -> int:
        return len(self.commits)

    @property
    def file_count(self) -> int:
        return len(self.files_changed)


@dataclass(frozen=True)
class ValidationResult:
    """The verdict from a :class:`Validator`."""

    passed: bool
    failure_reason: str | None = None

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(passed=True, failure_reason=None)

    @classmethod
    def fail(cls, reason: str) -> "ValidationResult":
        return cls(passed=False, failure_reason=reason)


@dataclass(frozen=True)
class ClassifierVote:
    """The verdict from a :class:`ClassifierRule`."""

    quadrant: Quadrant
    reasoning: str


@dataclass(frozen=True)
class BranchHookResult:
    """The verdict from a :class:`BranchHook`.

    If ``incident_label`` is ``None``, the hook reports no incident for this
    commit and the watermark may advance.
    """

    incident_label: str | None
    incident_body: str | None

    @classmethod
    def none(cls) -> "BranchHookResult":
        return cls(incident_label=None, incident_body=None)
