"""Optional Decision Inbox lifecycle (reminders, availability, return digest).

The lifecycle is deliberately default-off.  These tests adapt the predecessor
bot's 72-hour / 7-day and availability regressions to the generic configuration
contract used by this repository.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from multiagent_protocol.config.loader import (
    AvailabilitySourceConfig,
    DecisionInboxLifecycleConfig,
)
from multiagent_protocol.decision_inbox import (
    ESCALATION_COMMENT_MARKER,
    REMINDER_COMMENT_MARKER,
    RETURN_DIGEST_COMMENT_MARKER,
    parse_availability,
    process_lifecycle,
)

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _availability_line(start: datetime, end: datetime, state: str = "outing") -> str:
    return f"[OWNER_AVAILABILITY] {_iso(start)} – {_iso(end)} {state}\n"


class LifecycleAPI:
    """Small stateful API double; posted marker comments survive a restart."""

    def __init__(self, *, created_at: datetime, availability_text: str | None = None):
        self.issue = {
            "number": 7,
            "created_at": _iso(created_at),
            "labels": [{"name": "decision:pending-owner"}],
        }
        self.availability_text = availability_text
        self.comments: dict[int, list[dict]] = {7: []}
        self.next_comment_id = 100
        self.calls: list[tuple] = []
        self.closed: list[tuple] = []

    def list_issues(self, owner, repo, *, labels=None, state="open"):
        self.calls.append(("list_issues", owner, repo, labels, state))
        return [self.issue]

    def list_issue_comments(self, owner, repo, number):
        self.calls.append(("list_issue_comments", owner, repo, number))
        return list(self.comments[number])

    def post_comment(self, owner, repo, number, body):
        self.calls.append(("post_comment", owner, repo, number, body))
        self.next_comment_id += 1
        self.comments[number].append(
            {
                "id": self.next_comment_id,
                "body": body,
                "user": {"login": "merge-gate[bot]", "type": "Bot"},
                "created_at": _iso(T0),
            }
        )

    def update_issue_comment(self, owner, repo, comment_id, body):
        self.calls.append(("update_issue_comment", owner, repo, comment_id, body))
        for comments in self.comments.values():
            for comment in comments:
                if comment.get("id") == comment_id:
                    comment["body"] = body
                    return
        raise AssertionError(f"unknown comment id {comment_id}")

    def get_file_text(self, owner, repo, path, ref="main"):
        self.calls.append(("get_file_text", owner, repo, path, ref))
        return self.availability_text

    def close_issue(self, owner, repo, number):
        self.closed.append((owner, repo, number))


def _lifecycle(*, enabled: bool = True, availability: bool = False):
    source = None
    if availability:
        source = AvailabilitySourceConfig(
            repository="example/operations",
            path="status/availability.md",
            ref="main",
            line_prefix="[OWNER_AVAILABILITY]",
        )
    return DecisionInboxLifecycleConfig(
        enabled=enabled,
        reminder_hours=72,
        escalate_hours=168,
        availability=source,
    )


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(hours=71, minutes=59), 0),
        (timedelta(hours=72), 1),
        (timedelta(hours=72, minutes=1), 1),
    ],
)
def test_reminder_boundary_71h59_72h_72h01(age, expected):
    now = T0 + timedelta(days=10)
    api = LifecycleAPI(created_at=now - age)

    actions = process_lifecycle(
        api, "example", "inbox", _lifecycle(), now=now
    )

    reminders = [c for c in api.comments[7] if c["body"].startswith("[REMINDER_72H]")]
    assert len(reminders) == expected
    assert len([a for a in actions if a.action == "reminder"]) == expected


def test_reminder_and_escalation_are_each_once_across_restarts_and_never_close():
    api = LifecycleAPI(created_at=T0)
    lifecycle = _lifecycle()

    first = process_lifecycle(
        api, "example", "inbox", lifecycle, now=T0 + timedelta(hours=72)
    )
    second = process_lifecycle(
        api, "example", "inbox", lifecycle, now=T0 + timedelta(hours=168)
    )
    third = process_lifecycle(
        api, "example", "inbox", lifecycle, now=T0 + timedelta(hours=200)
    )

    bodies = [comment["body"] for comment in api.comments[7]]
    assert len([body for body in bodies if REMINDER_COMMENT_MARKER in body]) == 1
    assert len([body for body in bodies if ESCALATION_COMMENT_MARKER in body]) == 1
    assert [action.action for action in first + second + third] == [
        "reminder",
        "escalation",
    ]
    assert api.closed == []


def test_active_outing_suppresses_ladder_and_freezes_elapsed_clock():
    start = T0 + timedelta(hours=60)
    end = start + timedelta(hours=12)
    api = LifecycleAPI(
        created_at=T0,
        availability_text=_availability_line(start, end),
    )

    actions = process_lifecycle(
        api,
        "example",
        "inbox",
        _lifecycle(availability=True),
        now=start + timedelta(hours=10),
    )

    assert actions == []
    assert api.comments[7] == []


def test_twelve_hour_outing_keeps_sixty_hour_clock_at_return_then_resumes():
    start = T0 + timedelta(hours=60)
    end = start + timedelta(hours=12)
    api = LifecycleAPI(
        created_at=T0,
        availability_text=_availability_line(start, end),
    )
    lifecycle = _lifecycle(availability=True)

    at_return = process_lifecycle(api, "example", "inbox", lifecycle, now=end)
    assert [action.action for action in at_return] == ["return-digest"]
    assert not any(REMINDER_COMMENT_MARKER in c["body"] for c in api.comments[7])

    # The digest marker persists the completed window. The source may move to
    # its current state after return without losing the 12-hour clock pause.
    api.availability_text = "[OWNER_AVAILABILITY] available\n"

    before_72_effective = process_lifecycle(
        api, "example", "inbox", lifecycle, now=end + timedelta(hours=11, minutes=59)
    )
    assert before_72_effective == []

    at_72_effective = process_lifecycle(
        api, "example", "inbox", lifecycle, now=end + timedelta(hours=12)
    )
    assert [action.action for action in at_72_effective] == ["reminder"]


def test_return_digest_is_exactly_once_per_issue_after_window():
    start = T0 + timedelta(hours=10)
    end = start + timedelta(hours=2)
    api = LifecycleAPI(
        created_at=T0,
        availability_text=_availability_line(start, end),
    )
    lifecycle = _lifecycle(availability=True)

    first = process_lifecycle(
        api, "example", "inbox", lifecycle, now=end + timedelta(minutes=1)
    )
    second = process_lifecycle(
        api, "example", "inbox", lifecycle, now=end + timedelta(hours=1)
    )

    assert [action.action for action in first] == ["return-digest"]
    assert second == []
    assert len(
        [c for c in api.comments[7] if RETURN_DIGEST_COMMENT_MARKER in c["body"]]
    ) == 1


def test_one_digest_persists_every_observed_completed_outing_window():
    first_start = T0 + timedelta(hours=10)
    first_end = first_start + timedelta(hours=10)
    second_start = T0 + timedelta(hours=30)
    second_end = second_start + timedelta(hours=10)
    api = LifecycleAPI(
        created_at=T0,
        availability_text=(
            _availability_line(first_start, first_end)
            + _availability_line(second_start, second_end)
        ),
    )
    lifecycle = _lifecycle(availability=True)

    digest = process_lifecycle(
        api, "example", "inbox", lifecycle, now=T0 + timedelta(hours=41)
    )
    assert [action.action for action in digest] == ["return-digest"]
    assert api.comments[7][0]["body"].count(RETURN_DIGEST_COMMENT_MARKER) == 2

    api.availability_text = "[OWNER_AVAILABILITY] available\n"
    reminder = process_lifecycle(
        api, "example", "inbox", lifecycle, now=T0 + timedelta(hours=92)
    )
    assert [action.action for action in reminder] == ["reminder"]


def test_later_outing_updates_same_digest_comment_without_posting_a_second_one():
    first_start = T0 + timedelta(hours=10)
    first_end = first_start + timedelta(hours=10)
    api = LifecycleAPI(
        created_at=T0,
        availability_text=_availability_line(first_start, first_end),
    )
    lifecycle = _lifecycle(availability=True)

    first = process_lifecycle(
        api, "example", "inbox", lifecycle, now=T0 + timedelta(hours=21)
    )
    assert [action.action for action in first] == ["return-digest"]

    second_start = T0 + timedelta(hours=30)
    second_end = second_start + timedelta(hours=10)
    api.availability_text = _availability_line(second_start, second_end)
    second = process_lifecycle(
        api, "example", "inbox", lifecycle, now=T0 + timedelta(hours=41)
    )

    assert second == []
    assert len(api.comments[7]) == 1
    assert api.comments[7][0]["body"].count(RETURN_DIGEST_COMMENT_MARKER) == 2
    assert len([call for call in api.calls if call[0] == "update_issue_comment"]) == 1

    api.availability_text = "[OWNER_AVAILABILITY] available\n"
    reminder = process_lifecycle(
        api, "example", "inbox", lifecycle, now=T0 + timedelta(hours=92)
    )
    assert [action.action for action in reminder] == ["reminder"]


def test_quiet_state_suppresses_comments_without_inventing_an_outing_window():
    api = LifecycleAPI(
        created_at=T0,
        availability_text="[OWNER_AVAILABILITY] quiet\n",
    )

    actions = process_lifecycle(
        api,
        "example",
        "inbox",
        _lifecycle(availability=True),
        now=T0 + timedelta(days=8),
    )

    assert actions == []
    assert api.comments[7] == []


@pytest.mark.parametrize("state", ["available", "quiet"])
def test_availability_line_parses_non_outing_states(state):
    snapshot = parse_availability(
        f"[OWNER_AVAILABILITY] {state}\n",
        line_prefix="[OWNER_AVAILABILITY]",
        now=T0,
    )
    assert snapshot.state == state
    assert snapshot.outing_windows == ()


@pytest.mark.parametrize("separator", ["-", "–", "—"])
def test_availability_line_parses_outing_window_separators(separator):
    start = T0 + timedelta(hours=1)
    end = start + timedelta(hours=2)
    text = f"[OWNER_AVAILABILITY] {_iso(start)} {separator} {_iso(end)} outing\n"

    snapshot = parse_availability(
        text, line_prefix="[OWNER_AVAILABILITY]", now=start + timedelta(hours=1)
    )

    assert snapshot.state == "outing"
    assert snapshot.in_outing is True
    assert snapshot.outing_windows == ((start, end),)


def test_enabled_false_is_a_true_noop_with_no_api_reads_or_writes():
    class TripwireAPI:
        def __getattr__(self, name):
            raise AssertionError(f"default-off lifecycle touched API method {name}")

    assert process_lifecycle(
        TripwireAPI(),
        "example",
        "inbox",
        _lifecycle(enabled=False),
        now=T0,
    ) == []


def test_marker_from_another_bot_cannot_suppress_current_app_comment():
    api = LifecycleAPI(created_at=T0)
    api.comments[7].append(
        {
            "body": f"[REMINDER_72H] copied marker\n{REMINDER_COMMENT_MARKER}",
            "user": {"login": "unrelated-app[bot]", "type": "Bot"},
            "created_at": _iso(T0),
        }
    )

    actions = process_lifecycle(
        api,
        "example",
        "inbox",
        _lifecycle(),
        now=T0 + timedelta(hours=72),
        bot_login="merge-gate[bot]",
    )

    assert [action.action for action in actions] == ["reminder"]
    assert len(api.comments[7]) == 2


def test_main_governance_work_does_not_invoke_lifecycle_when_disabled(monkeypatch):
    """The orchestrator preserves the old tick path, not merely the helper."""
    from pathlib import Path
    from types import SimpleNamespace

    import multiagent_protocol.main as main_module

    monkeypatch.setattr(main_module, "MIRROR_PATHS", Path("does-not-exist.json"))
    monkeypatch.setattr(main_module, "resolve_open_issues", lambda *args: [])

    def lifecycle_tripwire(*args, **kwargs):
        raise AssertionError("default-off main path invoked lifecycle")

    monkeypatch.setattr(main_module, "process_lifecycle", lifecycle_tripwire)
    config = SimpleNamespace(
        projects=SimpleNamespace(
            decision_inbox=SimpleNamespace(lifecycle=_lifecycle(enabled=False))
        )
    )

    main_module._run_governance_work(
        object(),
        config,
        "example",
        "governance",
        "example",
        "inbox",
        [],
        ("decision-owner",),
        {"inbox_resolved": 0},
        lambda *args: True,
        T0,
        "merge-gate[bot]",
    )
