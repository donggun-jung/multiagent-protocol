"""Tests for the R3 unauthorized-push detector (code-level branch protection).

Flags any commit on ``main`` whose committer is NOT the bot, whose subject is
NOT break-glass, and whose committer login is NOT in the owner allowlist →
``decision:unauthorized-push`` incident. Sanctioned writes (bot merges,
break-glass commits, allowlisted-actor pushes) produce no incident.
"""

from __future__ import annotations

from multiagent_protocol.branch_supervisor import scan_repo
from multiagent_protocol.skills.builtin.hook_unauthorized_push import (
    INCIDENT_LABEL,
    UnauthorizedPushHook,
)
from multiagent_protocol.types import CommitContext, TrailerSet

BOT = "my-merge-gate-bot[bot]"
OWNER = "your-github-login"


def _commit(
    subject: str,
    *,
    committer_login: str | None,
    sha: str = "a" * 40,
) -> CommitContext:
    return CommitContext(
        sha=sha,
        subject=subject,
        body="",
        author_login=committer_login,
        committer_login=committer_login,
        parents=("p" * 40,),
        trailers=TrailerSet(),
    )


def _hook() -> UnauthorizedPushHook:
    return UnauthorizedPushHook(bot_user=BOT, allowlisted_actors=(OWNER,))


# -- unit: incident / no-incident ---------------------------------------------

def test_non_bot_non_breakglass_non_allowlisted_opens_incident():
    r = _hook().on_commit(_commit("feat: sneaky change", committer_login="mallory"))
    assert r.incident_label == INCIDENT_LABEL
    assert "mallory" in r.incident_body


def test_bot_squash_merge_no_incident():
    r = _hook().on_commit(
        _commit("feat: legitimate (#42)", committer_login=BOT))
    assert r.incident_label is None


def test_allowlisted_break_glass_no_incident():
    # A break-glass commit is the L5 break-glass auditor's job; this hook skips
    # it (so a legit break-glass commit is not double-incidented).
    r = _hook().on_commit(
        _commit("[break-glass-security] revoke key", committer_login=OWNER))
    assert r.incident_label is None


def test_non_allowlisted_break_glass_still_skipped_here():
    # Even a break-glass commit by a NON-allowlisted actor is skipped by THIS
    # hook — the break-glass auditor opens 'break-glass-unauthorized' for it, so
    # this hook deferring avoids a duplicate incident on the same commit.
    r = _hook().on_commit(
        _commit("[break-glass-x] hotfix", committer_login="bystander"))
    assert r.incident_label is None


def test_allowlisted_direct_push_no_incident():
    r = _hook().on_commit(_commit("docs: tweak", committer_login=OWNER))
    assert r.incident_label is None


def test_unknown_committer_login_opens_incident():
    # A commit whose committer login could not be resolved is not the bot, not
    # break-glass, and not allowlisted → unsanctioned.
    r = _hook().on_commit(_commit("feat: who pushed this", committer_login=None))
    assert r.incident_label == INCIDENT_LABEL


def test_unconfigured_hook_safe_no_op_for_break_glass():
    # The 0-arg loader instance (no bot user, empty allowlist) is never used for
    # a real scan, but it must still not crash. A break-glass commit is skipped
    # regardless of configuration.
    r = UnauthorizedPushHook().on_commit(
        _commit("[break-glass-x] y", committer_login="anyone"))
    assert r.incident_label is None


# -- integration: scan_repo idempotency ---------------------------------------

def _raw(sha: str, subject: str, committer: str | None) -> dict:
    return {
        "sha": sha,
        "commit": {"message": subject, "committer": {"date": "2026-05-25T00:00:00Z"}},
        "author": {"login": committer},
        "committer": {"login": committer} if committer is not None else None,
        "parents": [{"sha": "p" * 40}],
    }


def test_scan_repo_flags_then_idempotent_on_second_scan(fake_api):
    # First scan over a fresh main with one unauthorized commit → one incident.
    fake_api.seed_main_commits(
        "o", "r", [_raw("u" * 40, "feat: unauthorized", "mallory")])
    hooks = [_hook()]

    incidents1, wm1 = scan_repo(fake_api, "o", "r", hooks, {})
    assert len(incidents1) == 1
    assert incidents1[0].label == INCIDENT_LABEL
    assert wm1 == "u" * 40

    # Second scan with the advanced watermark → no NEW commits → no re-flag.
    incidents2, wm2 = scan_repo(fake_api, "o", "r", hooks, {"o/r": wm1})
    assert incidents2 == []
    assert wm2 == "u" * 40


def test_scan_repo_mixed_commits_only_flags_unsanctioned(fake_api):
    # A bot merge, a break-glass commit, and one unauthorized push on main.
    fake_api.seed_main_commits("o", "r", [
        _raw("c" * 40, "feat: unauthorized", "mallory"),     # newest
        _raw("b" * 40, "[break-glass-x] hotfix", OWNER),
        _raw("a" * 40, "feat: merged (#1)", BOT),            # oldest
    ])
    incidents, _ = scan_repo(fake_api, "o", "r", [_hook()], {})
    labels = [(i.commit_sha, i.label) for i in incidents]
    assert labels == [("c" * 40, INCIDENT_LABEL)]
