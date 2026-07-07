"""FEATURE A — L2 auto-revert wiring through main() (env.yml auto_revert_pr).

These drive the whole tick (``main.main``) with the in-memory FakeAPI, so they
exercise ``_open_l2_capped``: incident-first ordering, the revert-PR call with
``Task-Ref: Issue#N``, the body-append, the metric, and idempotency — WITHOUT
running real git (``ensure_revert_pr`` is monkeypatched to a spy, mirroring how
the reliability suite monkeypatches ``build_runtime_skills`` / ``GitHubAPI``).

Disabled path: with ``auto_revert_pr`` absent/false, the L2 real-failure
incident opens exactly as v1.1 and ``ensure_revert_pr`` is never called.
"""

from __future__ import annotations

from pathlib import Path

import multiagent_protocol.main as main_mod
from multiagent_protocol.auto_revert import RevertResult
from tests.conftest import FakeAPI, make_check, raw_commit

HEAD = "m" * 40
BOT = "acme-merge-gate[bot]"


class _FakeAuth:
    def __init__(self, account: str = "acme") -> None:
        self._account = account

    def installations(self):
        return [{"id": 1, "account": {"login": self._account}}]

    def installation_token(self, installation_id):
        return "fake-install-token-xyz"

    def app_slug(self):
        # Matches env.bot_app_slug so _resolve_bot_user builds <slug>[bot]
        # without warning and build_runtime_skills succeeds.
        return "acme-merge-gate"


def _write_config(cfg_dir: Path, *, auto_revert: bool) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "owner.yml").write_text("github_login: acme\n", encoding="utf-8")
    (cfg_dir / "projects.yml").write_text(
        "governance_repo: acme/governance\n"
        "supervised_repos:\n"
        "  - acme/governance\n"
        "  - acme/app\n",
        encoding="utf-8",
    )
    env = "bot_app_slug: acme-merge-gate\nallow_no_ci: true\n"
    if auto_revert:
        env += "auto_revert_pr: true\n"
    (cfg_dir / "env.yml").write_text(env, encoding="utf-8")


def _api_auth(fake_api: FakeAPI) -> FakeAPI:
    # main() calls _installation_token(api) which reads api.auth + api.installation_id.
    fake_api.auth = _FakeAuth()
    fake_api.installation_id = 1
    return fake_api


def _seed_one_real_failure(fake_api: FakeAPI) -> str:
    """acme/app: one merged commit on main whose required check failed (real
    failure), with L2 watermark just behind it so L2 emits one incident."""
    base = "ba5e" + "0" * 36
    bad = "bad0" + "0" * 36
    fake_api.seed_main_commits("acme", "app", [
        raw_commit(sha=bad, author=BOT), raw_commit(sha=base, author=BOT),
    ])
    fake_api._checks[bad] = [make_check("test", "failure")]
    # governance: already activated + past HEAD (no L2/L5 work there).
    fake_api.seed_bot_state("acme", "governance", {
        "acme/governance": HEAD, "acme/governance:l2": HEAD,
        "acme/app": bad,          # L5 already past it (no L5 incident)
        "acme/app:l2": base,      # L2 sees the bad commit
    })
    return bad


def _run(tmp_path, monkeypatch, fake_api, *, auto_revert: bool, spy=None):
    _write_config(tmp_path / "config", auto_revert=auto_revert)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MERGE_GATE_APP_ID", "123")
    monkeypatch.setenv("MERGE_GATE_PRIVATE_KEY", "dummy-pem")
    monkeypatch.setattr(main_mod.AppAuth, "from_env",
                        classmethod(lambda cls, *a, **k: _FakeAuth("acme")))
    monkeypatch.setattr(main_mod, "GitHubAPI", lambda auth, inst_id: fake_api)
    if spy is not None:
        monkeypatch.setattr(main_mod, "ensure_revert_pr", spy)
    return main_mod.main([])


def test_auto_revert_on_opens_incident_and_revert_pr_and_links_it(tmp_path, monkeypatch):
    fake_api = _api_auth(FakeAPI(main_head=HEAD))
    bad = _seed_one_real_failure(fake_api)

    calls: list[dict] = []

    def _spy(api, owner, name, sha, *, token, incident_ref, **kw):
        calls.append({"owner": owner, "name": name, "sha": sha,
                      "token": token, "incident_ref": incident_ref})
        return RevertResult(
            pr_url="https://github.com/acme/app/pull/77", created=True,
            note="Auto-revert PR opened (goes through the normal gate): "
                 "https://github.com/acme/app/pull/77",
        )

    rc = _run(tmp_path, monkeypatch, fake_api, auto_revert=True, spy=_spy)
    assert rc == 0

    # Exactly ONE post-merge-revalidation incident was opened.
    revalidation = [i for i in fake_api.issues_opened
                    if "decision:post-merge-revalidation" in i["_labels"]]
    assert len(revalidation) == 1
    issue = revalidation[0]

    # The revert PR was requested for the right repo/commit, with the incident
    # issue as Task-Ref, using the installation token.
    assert len(calls) == 1
    c = calls[0]
    assert (c["owner"], c["name"], c["sha"]) == ("acme", "app", bad)
    assert c["incident_ref"] == f"Issue#{issue['number']}"
    assert c["token"] == "fake-install-token-xyz"

    # The PR link was appended to the incident body (idempotent update surface).
    updated = [b for (o, r, n, b) in fake_api.issue_bodies_updated
               if n == issue["number"]]
    assert updated and "pull/77" in updated[-1]


def test_auto_revert_off_opens_incident_but_no_revert_pr(tmp_path, monkeypatch):
    fake_api = _api_auth(FakeAPI(main_head=HEAD))
    _seed_one_real_failure(fake_api)

    called = []

    def _spy(*a, **k):
        called.append(1)
        return RevertResult(None, False, "should not happen")

    rc = _run(tmp_path, monkeypatch, fake_api, auto_revert=False, spy=_spy)
    assert rc == 0
    # The incident still opens (v1.1 behavior preserved)…
    assert any("decision:post-merge-revalidation" in i["_labels"]
               for i in fake_api.issues_opened)
    # …but the auto-revert path was never entered.
    assert called == []
    assert fake_api.issue_bodies_updated == []


def test_auto_revert_idempotent_when_incident_already_open(tmp_path, monkeypatch):
    # The incident issue already exists (a prior tick opened it, then died
    # before advancing the L2 watermark). This tick must NOT open a duplicate
    # issue, must NOT spend budget, but MUST still (idempotently) ensure the
    # revert PR — ensure_revert_pr is called with the EXISTING issue's ref.
    fake_api = _api_auth(FakeAPI(main_head=HEAD))
    bad = _seed_one_real_failure(fake_api)
    # Pre-seed the incident, exactly as _open_incident_if_new would title it,
    # with the dedupe marker present.
    marker = main_mod._DEDUPE_MARKER.format(key=bad[:7])
    pre = fake_api.seed_issue(
        labels=("decision:post-merge-revalidation",),
        title=f"[decision:post-merge-revalidation] {bad[:7]}",
        body=f"post-merge failure\n\n{marker}",
    )

    calls = []

    def _spy(api, owner, name, sha, *, token, incident_ref, **kw):
        calls.append(incident_ref)
        return RevertResult("https://github.com/acme/app/pull/88", True, "opened: pull/88")

    rc = _run(tmp_path, monkeypatch, fake_api, auto_revert=True, spy=_spy)
    assert rc == 0
    # No NEW incident opened (deduped against the pre-seeded one).
    assert fake_api.issues_opened == []
    # Still ensured the revert PR, bound to the EXISTING incident's number.
    assert calls == [f"Issue#{pre['number']}"]
