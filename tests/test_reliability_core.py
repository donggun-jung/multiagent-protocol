"""RELIABILITY-CORE regression tests (the W1 hardening pass).

Pins the durability/correctness behaviors added after the live cold-start
incident flood + 5-minute-tick timeout:

- bootstrap-to-HEAD on an empty watermark (scan NOTHING pre-activation),
- durable watermark persistence on the dedicated ``bot-state`` branch,
- bounded, monotonic per-tick scan progress,
- L2 stall escalation (a permanently-``cancelled`` commit cannot silently
  halt L2 forever; ``skipped`` on a REQUIRED check is not success),
- idempotent diagnostics against open AND closed issues (no zombie reopen),
- fail-closed handling of corrupt persisted state (never a silent re-bootstrap),
- capped-since-search recovery (watermark lost → ONE incident + re-bootstrap,
  no full-history replay),
- secondary-rate-limit (403/429) back-off instead of crash-and-replay,
- drift efficiency: one tree fetch per repo per tick, blob-SHA equality,
  content-derived drift dedupe key.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import multiagent_protocol.github_api as github_api_mod
import multiagent_protocol.main as main_mod
from multiagent_protocol.branch_supervisor import (
    BOT_STATE_PATH,
    BotStateStore,
    count_l2_unsettled,
    load_watermarks,
    revalidate_main,
    scan_repo,
)
from multiagent_protocol.drift_check import (
    DriftIncident,
    MirrorConfig,
    check_repo_against_canonical,
)
from multiagent_protocol.github_api import (
    SINCE_NOT_FOUND,
    GitHubAPI,
    SecondaryRateLimitError,
)
from multiagent_protocol.main import (
    _drift_dedupe_key,
    _DriftTreeAPI,
    _open_incident_if_new,
    _rollup_incidents,
)
from tests.conftest import FakeAPI, make_check, raw_commit

HEAD = "m" * 40
BOT = "acme-merge-gate[bot]"


# ---------------------------------------------------------------------------
# main()-driven harness (mirrors tests/test_audit_only.py, kept local so this
# file does not couple to that module's fixtures).
# ---------------------------------------------------------------------------


class _FakeAuth:
    def __init__(self, account: str) -> None:
        self._account = account

    def installations(self) -> list[dict]:
        return [{"id": 1, "account": {"login": self._account}}]


def _write_config(cfg_dir: Path) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "owner.yml").write_text("github_login: acme\n", encoding="utf-8")
    (cfg_dir / "projects.yml").write_text(
        "governance_repo: acme/governance\n"
        "supervised_repos:\n"
        "  - acme/governance\n"
        "  - acme/app\n",
        encoding="utf-8",
    )
    (cfg_dir / "env.yml").write_text(
        "bot_app_slug: acme-merge-gate\nallow_no_ci: true\n", encoding="utf-8")


def _run_main(tmp_path, monkeypatch, fake_api: FakeAPI) -> int:
    _write_config(tmp_path / "config")
    monkeypatch.chdir(tmp_path)  # config/ read from cwd; no schemas/ → skipped
    monkeypatch.setenv("MERGE_GATE_APP_ID", "123")
    monkeypatch.setenv("MERGE_GATE_PRIVATE_KEY", "dummy-pem")
    monkeypatch.setattr(main_mod.AppAuth, "from_env",
                        classmethod(lambda cls, *a, **k: _FakeAuth("acme")))
    monkeypatch.setattr(main_mod, "GitHubAPI", lambda auth, inst_id: fake_api)
    return main_mod.main([])


def _persisted_state(fake_api: FakeAPI) -> dict:
    assert fake_api.bot_state_writes, "no durable bot-state write happened"
    return json.loads(fake_api.bot_state_writes[-1][4])


# ---------------------------------------------------------------------------
# 1. Bootstrap on empty watermark: the cold-start flood regression.
# ---------------------------------------------------------------------------


def test_bootstrap_empty_watermark_scans_nothing_and_persists_head(
    tmp_path, monkeypatch,
):
    # A repo with N historical commits that would each have raised an
    # unauthorized-push incident under the old cold-start full-history walk.
    fake_api = FakeAPI(main_head=HEAD)
    fake_api.seed_main_commits("acme", "governance", [
        raw_commit(sha=f"{i:02d}" + "a" * 38, author="mallory") for i in range(5)
    ])

    rc = _run_main(tmp_path, monkeypatch, fake_api)
    assert rc == 0
    # ZERO diagnostic issues: pre-activation history is out of scope.
    assert fake_api.issues_opened == []
    # The watermark was bootstrapped to current main HEAD for both layers of
    # both supervised repos and durably persisted.
    state = _persisted_state(fake_api)
    for key in ("acme/governance", "acme/governance:l2", "acme/app", "acme/app:l2"):
        assert state[key] == HEAD
    # All durable writes went to the dedicated bot-state branch — never main.
    assert all(w[2] == "bot-state" for w in fake_api.bot_state_writes)
    assert fake_api.refs_created == [("acme", "governance", "bot-state", HEAD)]


# ---------------------------------------------------------------------------
# 2. Watermark persistence across ticks (no per-tick cold start).
# ---------------------------------------------------------------------------


def test_watermark_persists_across_two_ticks(tmp_path):
    fake_api = FakeAPI(main_head=HEAD)
    local = tmp_path / "wm.json"

    # Tick 1: nothing persisted yet → branch created, state starts empty.
    store1 = BotStateStore(fake_api, "acme", "governance", local_path=local)
    wm = store1.load()
    assert wm == {}
    wm["acme/app"] = "f" * 40
    store1.save(wm)
    assert len(fake_api.bot_state_writes) == 1

    # Tick 2: a FRESH store (new process, clean workspace) loads tick 1's value
    # from the bot-state branch — no cold start.
    store2 = BotStateStore(fake_api, "acme", "governance", local_path=local)
    assert store2.load() == {"acme/app": "f" * 40}

    # Everything durable lives on the dedicated branch.
    assert all(w[2] == "bot-state" for w in fake_api.bot_state_writes)
    assert all(w[3] == BOT_STATE_PATH for w in fake_api.bot_state_writes)


def test_bot_state_save_refreshes_lost_blob_sha(tmp_path):
    # After a failed/raced push the cached blob SHA is dropped; the next save
    # must RE-READ it (a bare create against an existing file is a 422 on the
    # real API, which would wedge every later save this tick).
    class _RecordingAPI(FakeAPI):
        def __init__(self):
            super().__init__(main_head=HEAD)
            self.put_blob_shas: list = []

        def put_file_on_ref(self, owner, repo, path, *, ref, content, message,
                            blob_sha=None):
            self.put_blob_shas.append(blob_sha)
            return super().put_file_on_ref(
                owner, repo, path, ref=ref, content=content,
                message=message, blob_sha=blob_sha)

    api = _RecordingAPI()
    api.seed_bot_state("acme", "governance", {"k": "v"})
    store = BotStateStore(api, "acme", "governance", local_path=tmp_path / "wm.json")
    loaded = store.load()
    store.save(loaded)                       # uses the blob sha cached by load()
    store._remote_blob_sha = None            # simulate a dropped precondition
    store.save(loaded)                       # must re-read, NOT put sha=None
    assert api.put_blob_shas[0] is not None
    assert api.put_blob_shas[1] is not None  # refreshed from the branch


# ---------------------------------------------------------------------------
# 3. Bounded per-tick processing, monotonic progress.
# ---------------------------------------------------------------------------


def test_scan_repo_bounded_per_tick_and_monotonic(fake_api):
    shas = [f"{i:03d}" + "a" * 37 for i in range(150)]      # 000.. oldest
    fake_api.seed_main_commits(
        "o", "r", [raw_commit(sha=s) for s in reversed(shas)])  # newest first

    watermarks: dict = {}
    _, wm1 = scan_repo(fake_api, "o", "r", [], watermarks)
    assert wm1 == shas[99]      # exactly the per-tick cap, oldest first
    watermarks["o/r"] = wm1

    _, wm2 = scan_repo(fake_api, "o", "r", [], watermarks)
    assert wm2 == shas[149]     # remainder drained next tick — no overlap, no gap


# ---------------------------------------------------------------------------
# 4. L2 stall: a permanently-cancelled commit escalates exactly once.
# ---------------------------------------------------------------------------


def test_l2_cancelled_stuck_past_deadline_escalates_exactly_once(fake_api):
    sha = "b" * 40
    fake_api.seed_main_commits("o", "r", [raw_commit(sha=sha)])
    fake_api._checks[sha] = [make_check("test", "cancelled")]
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    watermarks: dict = {}

    # Tick 1 (T0): within grace → unsettled, no incident, watermark held.
    inc1, wm1 = revalidate_main(fake_api, "o", "r", (), watermarks,
                                clock=lambda: t0)
    assert inc1 == [] and wm1 is None
    assert count_l2_unsettled(watermarks) == 1

    # Tick 2 (T0+25h): past the 24h deadline → exactly ONE stall diagnostic,
    # commit force-settled, watermark advances (L2 resumes for newer commits).
    t1 = t0.replace(day=2, hour=1)
    inc2, wm2 = revalidate_main(fake_api, "o", "r", (), watermarks,
                                clock=lambda: t1)
    assert [i.label for i in inc2] == ["decision:l2-stalled"]
    assert inc2[0].commit_sha == sha
    assert wm2 == sha
    assert count_l2_unsettled(watermarks) == 0
    watermarks["o/r:l2"] = wm2

    # Tick 3: settled — no infinite retry, no second diagnostic.
    inc3, _ = revalidate_main(fake_api, "o", "r", (), watermarks,
                              clock=lambda: t1.replace(hour=2))
    assert inc3 == []


def test_l2_skipped_required_check_is_not_success(fake_api):
    # A REQUIRED check that resolves ``skipped`` never ran: it must not settle
    # the commit as passed (it rides the stall deadline instead).
    sha = "c" * 40
    fake_api.seed_main_commits("o", "r", [raw_commit(sha=sha)])
    fake_api._checks[sha] = [make_check("build", "skipped")]
    watermarks: dict = {}
    incidents, wm = revalidate_main(fake_api, "o", "r", ("build",), watermarks)
    assert incidents == []
    assert wm is None                          # NOT advanced — not a pass
    assert count_l2_unsettled(watermarks) == 1  # tracked toward the deadline


# ---------------------------------------------------------------------------
# 6. Idempotent diagnostics: a closed issue is not reopened (state=all dedupe).
# ---------------------------------------------------------------------------


def test_closed_diagnostic_not_reopened_next_tick(fake_api):
    label, key = "decision:post-merge-revalidation", "abc1234"
    assert _open_incident_if_new(fake_api, "acme", "governance", label, "body", key)
    number = fake_api.issues_opened[-1]["number"]

    # The owner closes the (false) diagnostic…
    fake_api.close_issue("acme", "governance", number)

    # …and the next tick must NOT zombie-reopen it.
    assert not _open_incident_if_new(fake_api, "acme", "governance", label, "body", key)
    assert len(fake_api.issues_opened) == 1


# ---------------------------------------------------------------------------
# 7. Corrupt persisted state fails CLOSED (never a silent re-bootstrap).
# ---------------------------------------------------------------------------


def test_corrupt_remote_bot_state_fails_tick_closed(tmp_path, monkeypatch):
    fake_api = FakeAPI(main_head=HEAD)
    fake_api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    fake_api._ref_files[("acme", "governance", "bot-state", BOT_STATE_PATH)] = (
        '{"acme/app": tru', "blob1")  # truncated mid-write

    with pytest.raises(RuntimeError, match="fail-closed"):
        _run_main(tmp_path, monkeypatch, fake_api)
    # Nothing was scanned, no issue opened, no state overwritten.
    assert fake_api.issues_opened == []
    assert fake_api.bot_state_writes == []


def test_corrupt_local_watermark_cache_fails_closed(tmp_path):
    p = tmp_path / "watermarks.json"
    p.write_text('{"o/r": "abc', encoding="utf-8")
    with pytest.raises(RuntimeError, match="fail-closed"):
        load_watermarks(p)
    # Valid JSON that is not an object is just as unusable.
    p.write_text('["not", "a", "dict"]', encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a JSON object"):
        load_watermarks(p)


# ---------------------------------------------------------------------------
# 7c. GPT P1b: BotStateStore persistence fails CLOSED on critical errors so a
# misconfiguration cannot silently disable L2/L5 by bootstrapping-to-HEAD.
# ---------------------------------------------------------------------------


def test_existing_branch_missing_state_file_fails_closed(tmp_path):
    # The bot-state branch EXISTS (ref present) but its state file is absent /
    # unreadable. This is NOT a fresh deployment — re-bootstrapping to empty here
    # would silently disable L2/L5 every tick. load() must fail closed (raise),
    # NOT return an empty dict.
    fake_api = FakeAPI(main_head=HEAD)
    fake_api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    # NOTE: no _ref_files entry → get_file_on_ref returns None on an EXISTING
    # branch (the silent-bootstrap hole this fix closes).
    store = BotStateStore(
        fake_api, "acme", "governance", local_path=tmp_path / "wm.json")
    with pytest.raises(RuntimeError, match="fail-closed"):
        store.load()
    # And no compensating write happened.
    assert fake_api.bot_state_writes == []
    assert fake_api.refs_created == []  # branch already existed; not re-created


def test_existing_branch_missing_state_file_aborts_whole_tick(tmp_path, monkeypatch):
    # End-to-end: the same condition aborts the tick (store.load() is unguarded
    # in main), scanning nothing and overwriting nothing — never a silent
    # re-bootstrap-to-HEAD.
    fake_api = FakeAPI(main_head=HEAD)
    fake_api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    with pytest.raises(RuntimeError, match="fail-closed"):
        _run_main(tmp_path, monkeypatch, fake_api)
    assert fake_api.issues_opened == []
    assert fake_api.bot_state_writes == []


def test_genuine_first_run_no_branch_starts_empty(tmp_path):
    # Contrast: when the branch does NOT exist (legitimate first-ever run), the
    # branch is created and the state legitimately starts empty — the one
    # "empty is fine" case must keep working.
    fake_api = FakeAPI(main_head=HEAD)
    store = BotStateStore(
        fake_api, "acme", "governance", local_path=tmp_path / "wm.json")
    assert store.load() == {}
    assert fake_api.refs_created  # branch was created off HEAD


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _PushError(Exception):
    """An HTTPError-shaped push failure (carries .response.status_code)."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.response = _Resp(status_code)


def test_hard_save_permission_error_raises(tmp_path):
    # GPT P1b(b): a write failure due to missing permission (403, e.g. no
    # contents:write) is non-transient — it would never succeed on retry, so a
    # silently-swallowed failure cold-starts L2/L5 forever. save() must RAISE
    # (fail the tick closed) instead of log-and-continue.
    class _ForbiddenAPI(FakeAPI):
        def put_file_on_ref(self, *a, **k):
            raise _PushError(403)

    api = _ForbiddenAPI(main_head=HEAD)
    api.seed_bot_state("acme", "governance", {"acme/app": "f" * 40})
    store = BotStateStore(api, "acme", "governance", local_path=tmp_path / "wm.json")
    store.load()
    with pytest.raises(_PushError):
        store.save({"acme/app": "g" * 40})


def test_transient_save_422_is_swallowed_and_retries(tmp_path):
    # Symmetric control: a stale-precondition 422 (a concurrent tick advanced the
    # file) is survivable — save() swallows it, drops the cached blob sha so the
    # NEXT save re-reads, and does NOT fail the tick.
    class _StaleAPI(FakeAPI):
        def __init__(self):
            super().__init__(main_head=HEAD)
            self.fail_next = True

        def put_file_on_ref(self, owner, repo, path, *, ref, content, message,
                            blob_sha=None):
            if self.fail_next:
                self.fail_next = False
                raise _PushError(422)
            return super().put_file_on_ref(
                owner, repo, path, ref=ref, content=content,
                message=message, blob_sha=blob_sha)

    api = _StaleAPI()
    api.seed_bot_state("acme", "governance", {"acme/app": "f" * 40})
    store = BotStateStore(api, "acme", "governance", local_path=tmp_path / "wm.json")
    store.load()
    store.save({"acme/app": "g" * 40})          # 422 → swallowed, no raise
    assert store._remote_blob_sha is None        # dropped for a clean re-read
    store.save({"acme/app": "g" * 40})           # next save succeeds
    assert api.bot_state_writes                   # the retry persisted


def test_hard_save_error_fails_whole_tick(tmp_path, monkeypatch):
    # End-to-end: a hard save failure surfaces through the finally-block persist
    # and aborts the tick (non-zero / raises), rather than being silently logged
    # and swallowed — which would let the next tick cold-start forever.
    class _ForbiddenAPI(FakeAPI):
        def put_file_on_ref(self, *a, **k):
            raise _PushError(403)

    fake_api = _ForbiddenAPI(main_head=HEAD)
    # Activate both supervised repos so the bootstrap path persists HEAD → save
    # fires this tick. seed_bot_state marks acme/governance activated; also seed
    # acme/app so neither repo is on the bootstrap-to-HEAD path... but the very
    # first persist is enough to trip the hard error regardless.
    fake_api.seed_bot_state("acme", "governance", {})
    with pytest.raises(_PushError):
        _run_main(tmp_path, monkeypatch, fake_api)


# ---------------------------------------------------------------------------
# 7d. Opus P2-1: one installation's transient runtime-build (GET /app slug)
# failure must not abort the whole tick and starve the healthy installations.
# ---------------------------------------------------------------------------


class _MultiAuth:
    def installations(self) -> list[dict]:
        # Two installations: acme (governance, healthy) + other (flaky slug).
        return [
            {"id": 1, "account": {"login": "acme"}},
            {"id": 2, "account": {"login": "other"}},
        ]


def test_flaky_installation_does_not_starve_healthy_ones(tmp_path, monkeypatch):
    # ``other``'s runtime build (GET /app slug) raises; ``acme`` is healthy. The
    # tick must skip ``other`` (fail-closed for it — no scan, no merge) yet still
    # process ``acme``'s repos, instead of one flaky /app aborting the fleet.
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "owner.yml").write_text("github_login: acme\n", encoding="utf-8")
    (cfg_dir / "projects.yml").write_text(
        "governance_repo: acme/governance\n"
        "supervised_repos:\n"
        "  - acme/governance\n"
        "  - acme/app\n"
        "  - other/svc\n",
        encoding="utf-8",
    )
    (cfg_dir / "env.yml").write_text(
        "bot_app_slug: acme-merge-gate\nallow_no_ci: true\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MERGE_GATE_APP_ID", "123")
    monkeypatch.setenv("MERGE_GATE_PRIVATE_KEY", "dummy-pem")
    monkeypatch.setattr(main_mod.AppAuth, "from_env",
                        classmethod(lambda cls, *a, **k: _MultiAuth()))

    # Distinct, account-tagged FakeAPIs per installation id.
    acme_api = FakeAPI(main_head=HEAD)
    acme_api._account = "acme"
    other_api = FakeAPI(main_head=HEAD)
    other_api._account = "other"
    by_id = {1: acme_api, 2: other_api}
    monkeypatch.setattr(main_mod, "GitHubAPI", lambda auth, inst_id: by_id[inst_id])

    # The flaky installation: building its runtime raises (simulates the
    # transient GET /app slug lookup that _resolve_bot_user fails closed on).
    real_build = main_mod.build_runtime_skills

    def _flaky_build(config, api, **kw):
        if getattr(api, "_account", None) == "other":
            raise RuntimeError("GET /app slug transiently unavailable")
        return real_build(config, api, **kw)

    monkeypatch.setattr(main_mod, "build_runtime_skills", _flaky_build)

    rc = main_mod.main([])
    assert rc == 0  # the fleet completed; the flaky installation did not abort it

    # acme's repos were bootstrapped/persisted; other/svc was NOT scanned at all
    # (its runtime build failed → the whole installation was skipped).
    persisted = _persisted_state(acme_api)
    assert "acme/app" in persisted and "acme/app:l2" in persisted
    assert "other/svc" not in persisted and "other/svc:l2" not in persisted
    # The flaky installation's client never did any bot-state write of its own.
    assert other_api.bot_state_writes == []


# ---------------------------------------------------------------------------
# 7b. Watermark fell off main history → ONE incident + re-bootstrap, no replay.
# ---------------------------------------------------------------------------


def test_watermark_lost_rebootstraps_with_one_incident():
    class _LostAPI(FakeAPI):
        def list_commits_on_main(self, owner, repo, since_sha=None):
            return SINCE_NOT_FOUND

    api = _LostAPI(main_head=HEAD)
    watermarks = {"o/r": "dead" * 10}
    incidents, wm = scan_repo(api, "o", "r", [], watermarks)
    assert [i.label for i in incidents] == ["decision:watermark-lost"]
    assert incidents[0].commit_sha == HEAD
    assert wm == HEAD and watermarks["o/r"] == HEAD

    l2 = {"o/r:l2": "dead" * 10}
    incidents2, wm2 = revalidate_main(api, "o", "r", (), l2)
    assert [i.label for i in incidents2] == ["decision:watermark-lost"]
    assert wm2 == HEAD and l2["o/r:l2"] == HEAD


def test_list_commits_since_sha_not_found_returns_sentinel():
    class _PagedAPI(GitHubAPI):
        def __init__(self, pages):
            self._pages = list(pages)

        def _request(self, method, path, *, params=None, json=None):
            class _R:
                status_code = 200

                def __init__(self, payload):
                    self._payload = payload

                def json(self):
                    return self._payload

                def raise_for_status(self):
                    return None
            return _R(self._pages.pop(0))

    # Anchor present → commits newer than it, never the sentinel.
    api = _PagedAPI([[{"sha": "aaa"}, {"sha": "bbb"}]])
    assert api.list_commits_on_main("o", "r", since_sha="bbb") == [{"sha": "aaa"}]

    # History EXHAUSTED without meeting the anchor (small repo, force-pushed
    # main): sentinel — returning the full list here would be the full replay.
    api = _PagedAPI([[{"sha": "aaa"}, {"sha": "bbb"}]])
    assert api.list_commits_on_main("o", "r", since_sha="zzz") is SINCE_NOT_FOUND

    # Page CAP hit without meeting the anchor (big repo): sentinel, and the
    # walk stops at the cap instead of paging on.
    one_full_page = [[{"sha": f"{i:03d}"} for i in range(100)]]
    api = _PagedAPI(list(one_full_page))
    assert (
        api.list_commits_on_main("o", "r", since_sha="zzz", max_pages=1)
        is SINCE_NOT_FOUND
    )

    # No anchor at all: the bounded walk returns the capped list (a cold scan
    # has no anchor to miss).
    api = _PagedAPI(list(one_full_page))
    capped = api.list_commits_on_main("o", "r", max_pages=1)
    assert isinstance(capped, list) and len(capped) == 100


# ---------------------------------------------------------------------------
# 5. Flood controls: rollup per (repo, label) + per-tick cap defers, not loses.
# ---------------------------------------------------------------------------


def test_rollup_collapses_per_label_with_stable_key():
    from multiagent_protocol.branch_supervisor import SupervisorIncident
    incs = [
        SupervisorIncident("a" * 40, "decision:unauthorized-push", "body-a"),
        SupervisorIncident("b" * 40, "decision:unauthorized-push", "body-b"),
        SupervisorIncident("c" * 40, "decision:unauthorized-push", "body-c"),
        SupervisorIncident("d" * 40, "decision:break-glass-violation", "body-d"),
    ]
    rolled = _rollup_incidents("o", "r", incs)
    assert len(rolled) == 2                      # one per label, NOT one per commit
    by_label = {label: (key, body) for label, key, body in rolled}
    key, body = by_label["decision:unauthorized-push"]
    assert key.startswith("rollup-r-")
    assert all(s * 12 in body for s in "abc")    # every offender listed
    # Single-offender label keeps the per-commit shape.
    assert by_label["decision:break-glass-violation"][0] == "d" * 7

    # Same offender set → same key (idempotent across ticks); a new offender
    # changes the key (fresh rollup).
    assert _rollup_incidents("o", "r", incs[:3])[0][1] == key
    extra = incs[:3] + [SupervisorIncident("e" * 40, "decision:unauthorized-push", "x")]
    assert _rollup_incidents("o", "r", extra)[0][1] != key


def test_issue_cap_defers_without_losing_incidents(tmp_path, monkeypatch):
    # 32 real post-merge failures in one tick vs the 30-issue cap: the 2
    # overflow incidents must be DEFERRED (watermark held), then surfaced on
    # the next tick — not silently dropped.
    fake_api = FakeAPI(main_head=HEAD)
    base = "ba5e" + "0" * 36
    shas = [f"{i:02d}" + "c" * 38 for i in range(32)]        # 00.. oldest
    commits = [raw_commit(sha=s, author=BOT) for s in reversed(shas)]
    commits.append(raw_commit(sha=base, author=BOT))         # anchor, oldest
    fake_api.seed_main_commits("acme", "governance", commits)
    for s in shas:
        fake_api._checks[s] = [make_check("test", "failure")]
    fake_api.seed_bot_state("acme", "governance", {
        "acme/governance": shas[-1],      # L5 already past these commits
        "acme/governance:l2": base,       # L2 sees all 32
        "acme/app": HEAD, "acme/app:l2": HEAD,
    })

    assert _run_main(tmp_path, monkeypatch, fake_api) == 0
    assert len(fake_api.issues_opened) == 30                 # capped
    state = _persisted_state(fake_api)
    assert state["acme/governance:l2"] == base               # held, not lost

    # Next tick: the 30 dedupe, the deferred 2 open, watermark advances.
    assert _run_main(tmp_path, monkeypatch, fake_api) == 0
    assert len(fake_api.issues_opened) == 32
    opened_keys = {i["title"].split()[-1] for i in fake_api.issues_opened}
    assert opened_keys == {s[:7] for s in shas}              # every incident surfaced
    assert _persisted_state(fake_api)["acme/governance:l2"] == shas[-1]


# ---------------------------------------------------------------------------
# 5c. Secondary rate limit: back off, do not crash-and-replay; bare 403 is
#     still a real error surface.
# ---------------------------------------------------------------------------


class _HResp:
    def __init__(self, status, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _CannedSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def request(self, method, url, **kw):
        self.calls += 1
        return self._responses.pop(0)


class _TokenAuth:
    def installation_token(self, installation_id):
        return "tok"


def _no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(github_api_mod.time, "sleep", sleeps.append)
    return sleeps


def test_secondary_rate_limit_backs_off_then_succeeds(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    session = _CannedSession([
        _HResp(403, headers={"Retry-After": "7"}),
        _HResp(200, headers={"X-RateLimit-Remaining": "4321"}),
    ])
    api = GitHubAPI(_TokenAuth(), 1, session=session)
    resp = api._request("GET", "/repos/o/r/commits")
    assert resp.status_code == 200
    assert sleeps == [7.0]                       # honoured Retry-After
    assert api.rate_limit_remaining == 4321      # tracked for the reserve check


def test_secondary_rate_limit_exhausts_to_typed_error(monkeypatch):
    _no_sleep(monkeypatch)
    session = _CannedSession([
        _HResp(403, headers={"Retry-After": "1"}) for _ in range(4)
    ])
    api = GitHubAPI(_TokenAuth(), 1, session=session)
    with pytest.raises(SecondaryRateLimitError):
        api._request("GET", "/repos/o/r/commits")
    assert session.calls == 4                    # bounded retries, then typed error


def test_bare_403_is_not_treated_as_rate_limit(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    session = _CannedSession([_HResp(403)])      # a missing-scope 403: no hints
    api = GitHubAPI(_TokenAuth(), 1, session=session)
    resp = api._request("GET", "/repos/o/r/commits")
    assert resp.status_code == 403               # surfaced, not retried/masked
    assert sleeps == [] and session.calls == 1


# ---------------------------------------------------------------------------
# 8. Drift efficiency + content-derived dedupe key.
# ---------------------------------------------------------------------------


class _TreeStubAPI:
    def __init__(self, trees):
        self._trees = trees
        self.tree_calls: list = []
        self.content_calls: list = []

    def get_tree_blob_shas(self, owner, repo, ref="main"):
        self.tree_calls.append((owner, repo))
        return self._trees.get((owner, repo))

    def get_file_sha256(self, owner, repo, path, ref="main"):
        self.content_calls.append((owner, repo, path))
        return "fallback-sha"


def test_drift_tree_api_fetches_canonical_tree_once_per_tick():
    mirror = MirrorConfig(canonical_paths=("a.md", "b.md"), exceptions={})
    stub = _TreeStubAPI({
        ("gov", "g"): {"a.md": "s1", "b.md": "s2"},
        ("gov", "r1"): {"a.md": "s1", "b.md": "s2"},   # clean adopter
        ("gov", "r2"): {"a.md": "s1"},                 # b.md missing
    })
    drift_api = _DriftTreeAPI(stub)

    d1 = check_repo_against_canonical(drift_api, "gov", "g", "gov", "r1", mirror)
    d2 = check_repo_against_canonical(drift_api, "gov", "g", "gov", "r2", mirror)
    assert d1 == []
    assert [(i.path, i.kind) for i in d2] == [("b.md", "missing")]
    # The canonical tree was fetched ONCE for the whole tick (not per adopter),
    # and no per-path content download happened at all.
    assert stub.tree_calls.count(("gov", "g")) == 1
    assert len(stub.tree_calls) == 3
    assert stub.content_calls == []


def test_drift_tree_api_falls_back_per_path_when_tree_unavailable():
    mirror = MirrorConfig(canonical_paths=("a.md",), exceptions={})
    stub = _TreeStubAPI({("gov", "g"): {"a.md": "s1"}})    # adopter tree: None
    drift_api = _DriftTreeAPI(stub)
    drift = check_repo_against_canonical(drift_api, "gov", "g", "gov", "r9", mirror)
    # Adopter answered via the per-path fallback (same blob-SHA value kind).
    assert stub.content_calls == [("gov", "r9", "a.md")]
    assert [(i.path, i.kind) for i in drift] == [("a.md", "differs")]


def test_drift_dedupe_key_is_content_derived():
    a = DriftIncident("o/r1", "a.md", "differs", "c1", "x1")
    b = DriftIncident("o/r1", "b.md", "missing", "c2", None)
    same = _drift_dedupe_key([a, b])
    assert same.startswith("mirror-drift-")
    assert _drift_dedupe_key([b, a]) == same          # order-insensitive
    c = DriftIncident("o/r2", "a.md", "differs", "c1", "x9")
    assert _drift_dedupe_key([a, c]) != same          # new state → new issue
