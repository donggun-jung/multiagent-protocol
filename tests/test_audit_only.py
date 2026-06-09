"""Tests for DEC-C: the audit-only repo tier.

An audit-only repo is EXCLUDED from the per-open-PR L1-L4 loop (its PRs are not
gated/merged) but still INCLUDED in branch_supervisor (L2 post-merge + L5
break-glass + the R3 unauthorized-push detector). Default: no repo is
audit-only, so v1.0.0 behavior is unchanged.

These drive ``main()`` end-to-end against the in-memory FakeAPI by monkeypatching
the App auth + GitHubAPI constructor, so the real per-repo loop runs.
"""

from __future__ import annotations

from pathlib import Path

import multiagent_protocol.main as main_mod
from tests.conftest import FakeAPI, changed_file, raw_commit


class _FakeAuth:
    """Stands in for AppAuth: one installation owned by ``account``."""

    def __init__(self, account: str) -> None:
        self._account = account

    def installations(self) -> list[dict]:
        return [{"id": 1, "account": {"login": self._account}}]


def _write_config(cfg_dir: Path, *, audit_only: bool) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "owner.yml").write_text("github_login: acme\n", encoding="utf-8")
    # The governance repo is also the bot repo (no bot_repo override), so PRs to
    # it are Quadrant D by classifier_bot_self_repo regardless of audit_only —
    # we therefore assert merges on the SEPARATE acme/app repo and use
    # acme/governance only for the audit-only / not-gated assertions.
    projects = (
        "governance_repo: acme/governance\n"
        "supervised_repos:\n"
        "  - acme/governance\n"
        "  - acme/app\n"
    )
    if audit_only:
        projects += "audit_only_repos:\n  - acme/governance\n"
    (cfg_dir / "projects.yml").write_text(projects, encoding="utf-8")
    (cfg_dir / "env.yml").write_text(
        "bot_app_slug: acme-merge-gate\nallow_no_ci: true\n", encoding="utf-8")


def _run_main(tmp_path, monkeypatch, fake_api: FakeAPI, *, audit_only: bool) -> int:
    _write_config(tmp_path / "config", audit_only=audit_only)
    # chdir so main() reads config/ here; no schemas/ dir → schema validation
    # skipped (keeps the fixture minimal — agent_registry.yml omitted).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MERGE_GATE_APP_ID", "123")
    monkeypatch.setenv("MERGE_GATE_PRIVATE_KEY", "dummy-pem")
    monkeypatch.setattr(main_mod.AppAuth, "from_env",
                        classmethod(lambda cls, *a, **k: _FakeAuth("acme")))
    monkeypatch.setattr(main_mod, "GitHubAPI", lambda auth, inst_id: fake_api)
    return main_mod.main([])


def _seed_gated_app_pr(fake_api: FakeAPI) -> None:
    # An ordinary (non-bot, non-governance) repo PR: Quadrant A, ready to merge.
    fake_api.register_pr(
        owner="acme", repo="app", number=8, labels=("ready-to-merge",),
        files=[changed_file("README.md")], head_sha="a8" + "0" * 38,
        label_actor="acme")


def _seed_governance_pr_and_rogue_main(fake_api: FakeAPI) -> None:
    # An open PR on the governance (== bot) repo.
    fake_api.register_pr(
        owner="acme", repo="governance", number=7, labels=("ready-to-merge",),
        files=[changed_file("README.md")], head_sha="a7" + "0" * 38,
        label_actor="acme")
    # An unauthorized commit on governance main (committer not the bot, not
    # break-glass, not allowlisted) → the R3 hook should flag it.
    fake_api.seed_main_commits("acme", "governance", [
        {
            "sha": "z" * 40,
            "commit": {"message": "feat: rogue", "committer": {"date": "2026-05-25T00:00:00Z"}},
            "author": {"login": "mallory"},
            "committer": {"login": "mallory"},
            "parents": [{"sha": "p" * 40}],
        },
    ])


def test_audit_only_repo_prs_not_gated_but_main_scanned(tmp_path, monkeypatch):
    fake_api = FakeAPI(main_head="m" * 40)
    _seed_governance_pr_and_rogue_main(fake_api)
    _seed_gated_app_pr(fake_api)

    rc = _run_main(tmp_path, monkeypatch, fake_api, audit_only=True)
    assert rc == 0

    # (a) The audit-only governance PR (#7) was NOT gated/merged; the ordinary
    # acme/app PR (#8) IS still gated and merged in the same tick.
    assert ("acme", "governance", 7, "a7" + "0" * 38) not in fake_api.merged
    assert ("acme", "app", 8, "a8" + "0" * 38) in fake_api.merged

    # (b) governance main IS still scanned: the R3 unauthorized-push hook opened
    # an incident for the rogue commit even though the repo is PR-audit-only.
    labels_opened = {n for i in fake_api.issues_opened for n in i["_labels"]}
    assert "decision:unauthorized-push" in labels_opened


def test_governance_pr_gated_when_not_audit_only(tmp_path, monkeypatch):
    # Without audit_only, the governance PR is gated (and routed to inbox as a
    # bot-self-repo Quadrant D — the v1.0.0 behavior), and the ordinary app PR
    # still merges. This pins that audit_only is what changes the gating.
    fake_api = FakeAPI(main_head="m" * 40)
    fake_api.register_pr(
        owner="acme", repo="governance", number=7, labels=("ready-to-merge",),
        files=[changed_file("README.md")], head_sha="a7" + "0" * 38,
        label_actor="acme")
    _seed_gated_app_pr(fake_api)

    rc = _run_main(tmp_path, monkeypatch, fake_api, audit_only=False)
    assert rc == 0
    # Governance PR gated → bot-self-repo D → inbox issue opened, not merged.
    assert ("acme", "governance", 7, "a7" + "0" * 38) not in fake_api.merged
    gov_inbox = any(
        "decision:pending-owner" in i["_labels"] for i in fake_api.issues_opened)
    assert gov_inbox
    # App PR still merges.
    assert ("acme", "app", 8, "a8" + "0" * 38) in fake_api.merged


def test_audit_only_repo_l2_still_runs(tmp_path, monkeypatch):
    # A real post-merge CI failure on governance main opens an L2 incident even
    # though the repo is audit-only (L2 is not skipped). A bot committer keeps
    # the R3 hook silent so we isolate the L2 incident.
    fake_api = FakeAPI(main_head="m" * 40)
    bad_sha = "y" * 40
    fake_api.seed_main_commits("acme", "governance", [
        raw_commit(sha=bad_sha, subject="feat: merged (#1)",
                   author="acme-merge-gate[bot]"),
    ])
    fake_api._checks[bad_sha] = [
        {"name": "test", "status": "completed", "conclusion": "failure",
         "started_at": "2026-05-25T00:00:00Z", "completed_at": "2026-05-25T00:01:00Z",
         "app": {"slug": "github-actions"}, "output": {"summary": ""}},
    ]

    rc = _run_main(tmp_path, monkeypatch, fake_api, audit_only=True)
    assert rc == 0
    labels_opened = {n for i in fake_api.issues_opened for n in i["_labels"]}
    assert "decision:post-merge-revalidation" in labels_opened
    # governance PRs are not gated (none seeded here); no merges.
    assert fake_api.merged == []
