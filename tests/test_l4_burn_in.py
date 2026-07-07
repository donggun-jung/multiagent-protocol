"""FEATURE B — L4 60-day burn-in auto-promotion (l4_burn_in.apply_burn_in).

Clock is injected (``now``); the logic never reads datetime.now(). Covers:
- off (days=0) → no state written, no promotion;
- on, first tick → state written once (advisory_started_at), stays advisory;
- idempotent across ticks → state not rewritten each tick;
- before threshold → advisory (P2); after threshold → promoted (P0), logged once;
- explicit severity_overrides wins in BOTH directions (P0 pin, P2 pin) → clock inert;
- missing / corrupt state file → rewrite, fail-safe advisory (never hard-block);
- end-to-end through main() with a real runtime (validator_agent_registry present).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import multiagent_protocol.main as main_mod
from multiagent_protocol.l4_burn_in import (
    L4_BURN_IN_PATH,
    L4BurnInStore,
    apply_burn_in,
)
from tests.conftest import FakeAPI

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
HEAD = "m" * 40


class _MockValidator:
    def __init__(self, severity: str = "P2") -> None:
        self.name = "validator_agent_registry"
        self.severity = severity


def _runtime(severity: str = "P2", overrides: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        validators=[_MockValidator(severity)],
        severity_overrides=overrides or {},
    )


def _cfg(days: int) -> SimpleNamespace:
    return SimpleNamespace(env=SimpleNamespace(l4_burn_in_days=days))


def _store(api: FakeAPI) -> L4BurnInStore:
    # The bot-state branch is created by BotStateStore.load() in a real tick; for
    # unit tests seed just the branch ref so put/get_file_on_ref work.
    api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    return L4BurnInStore(api, "acme", "governance")


def _severity(rt: SimpleNamespace) -> str:
    return rt.validators[0].severity


def _persisted(api: FakeAPI) -> dict | None:
    entry = api._ref_files.get(
        ("acme", "governance", "bot-state", L4_BURN_IN_PATH))
    return json.loads(entry[0]) if entry else None


# -- off -----------------------------------------------------------------------

def test_disabled_writes_nothing_and_stays_advisory():
    api = _store(FakeAPI(main_head=HEAD)).api
    store = L4BurnInStore(api, "acme", "governance")
    rt = _runtime("P2")
    out = apply_burn_in(rt, _cfg(0), store, now=T0)
    assert out.active is False and out.just_promoted is False
    assert _severity(rt) == "P2"
    assert api.bot_state_writes == []          # no clock written
    assert _persisted(api) is None


# -- on: first tick writes the clock once, stays advisory ----------------------

def test_first_tick_writes_start_and_stays_advisory():
    api = FakeAPI(main_head=HEAD)
    store = _store(api)
    rt = _runtime("P2")
    out = apply_burn_in(rt, _cfg(60), store, now=T0)
    assert out.active is False              # not yet at threshold
    assert _severity(rt) == "P2"            # advisory this tick
    state = _persisted(api)
    assert state == {"advisory_started_at": "2026-06-01T00:00:00Z"}


def test_state_written_once_idempotent_across_ticks():
    api = FakeAPI(main_head=HEAD)
    store = _store(api)
    # Tick 1 writes the start.
    apply_burn_in(_runtime("P2"), _cfg(60), store, now=T0)
    n1 = len(api.bot_state_writes)
    assert n1 == 1
    # Tick 2, a day later (still advisory): the start is unchanged → NO new write.
    # (A fresh store reads the persisted start from the branch, as a new process
    # would.)
    store2 = L4BurnInStore(api, "acme", "governance")
    out = apply_burn_in(_runtime("P2"), _cfg(60), store2, now=T0 + timedelta(days=1))
    assert out.active is False
    assert len(api.bot_state_writes) == n1     # no second write
    assert _persisted(api)["advisory_started_at"] == "2026-06-01T00:00:00Z"


# -- before / after threshold --------------------------------------------------

def test_before_threshold_advisory_after_threshold_promoted():
    api = FakeAPI(main_head=HEAD)
    # Seed a start 59 days ago.
    started = (T0 - timedelta(days=59))
    api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    api._ref_files[("acme", "governance", "bot-state", L4_BURN_IN_PATH)] = (
        json.dumps({"advisory_started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ")}),
        "blob1",
    )
    # 59 days elapsed, window 60 → still advisory.
    rt = _runtime("P2")
    out = apply_burn_in(rt, _cfg(60), L4BurnInStore(api, "acme", "governance"), now=T0)
    assert out.active is False and _severity(rt) == "P2"

    # 61 days elapsed → promoted to P0, logged once.
    rt2 = _runtime("P2")
    out2 = apply_burn_in(
        rt2, _cfg(60), L4BurnInStore(api, "acme", "governance"),
        now=started + timedelta(days=61),
    )
    assert out2.active is True and out2.just_promoted is True
    assert _severity(rt2) == "P0"
    assert "promoted_at" in _persisted(api)


def test_promotion_logs_once_across_ticks():
    api = FakeAPI(main_head=HEAD)
    started = T0 - timedelta(days=100)
    api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    api._ref_files[("acme", "governance", "bot-state", L4_BURN_IN_PATH)] = (
        json.dumps({"advisory_started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ")}),
        "blob1",
    )
    # Tick A: crosses the threshold → just_promoted True, promoted_at written.
    outA = apply_burn_in(_runtime("P2"), _cfg(60),
                         L4BurnInStore(api, "acme", "governance"), now=T0)
    assert outA.active and outA.just_promoted
    # Tick B: already promoted (promoted_at present) → active, but NOT re-logged.
    outB = apply_burn_in(_runtime("P2"), _cfg(60),
                         L4BurnInStore(api, "acme", "governance"),
                         now=T0 + timedelta(days=1))
    assert outB.active and outB.just_promoted is False


# -- explicit override always wins (both directions) --------------------------

def test_explicit_override_p0_wins_clock_inert():
    # Operator pinned P0 via severity_overrides. build_runtime_skills already set
    # it; the clock must not manage the file at all.
    api = FakeAPI(main_head=HEAD)
    store = _store(api)
    rt = _runtime("P0", overrides={"validator_agent_registry": "P0"})
    out = apply_burn_in(rt, _cfg(60), store, now=T0)
    assert out.active is False and out.just_promoted is False
    assert "pinned" in out.reason
    assert _severity(rt) == "P0"           # left as the operator set it
    assert api.bot_state_writes == []      # clock inert — no file managed
    assert _persisted(api) is None


def test_explicit_override_p2_wins_even_past_threshold():
    # Operator explicitly pinned advisory P2. Even if the (unmanaged) window
    # would have elapsed, the clock does nothing — explicit config wins.
    api = FakeAPI(main_head=HEAD)
    store = _store(api)
    rt = _runtime("P2", overrides={"validator_agent_registry": "P2"})
    out = apply_burn_in(rt, _cfg(60), store, now=T0 + timedelta(days=365))
    assert out.active is False
    assert _severity(rt) == "P2"           # stays advisory by operator choice
    assert api.bot_state_writes == []


# -- fail-safe: missing / corrupt state → rewrite, advisory --------------------

def test_missing_state_file_rewrites_and_stays_advisory():
    # Branch exists but no state file yet (first activation on an existing
    # deployment). apply_burn_in writes a fresh start and stays advisory.
    api = FakeAPI(main_head=HEAD)
    api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    rt = _runtime("P2")
    out = apply_burn_in(rt, _cfg(60), L4BurnInStore(api, "acme", "governance"), now=T0)
    assert out.active is False and _severity(rt) == "P2"
    assert _persisted(api) == {"advisory_started_at": "2026-06-01T00:00:00Z"}


def test_corrupt_state_file_rewrites_and_stays_advisory():
    # A corrupt clock must NEVER hard-block: it is rewritten with a fresh start
    # and the gate stays advisory (fail-SAFE — the opposite of the watermark
    # store, which fails closed).
    api = FakeAPI(main_head=HEAD)
    api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    api._ref_files[("acme", "governance", "bot-state", L4_BURN_IN_PATH)] = (
        '{"advisory_started_at": tru', "blob-corrupt")   # truncated JSON
    rt = _runtime("P2")
    out = apply_burn_in(rt, _cfg(60), L4BurnInStore(api, "acme", "governance"), now=T0)
    assert out.active is False              # did NOT promote off garbage
    assert _severity(rt) == "P2"
    assert _persisted(api) == {"advisory_started_at": "2026-06-01T00:00:00Z"}


def test_read_failure_is_fail_safe_empty():
    class _RaiseAPI(FakeAPI):
        def get_file_on_ref(self, *a, **k):
            raise RuntimeError("transient read error")

    api = _RaiseAPI(main_head=HEAD)
    api._refs[("acme", "governance", "bot-state")] = "base" + "0" * 36
    rt = _runtime("P2")
    # A read failure surfaces as empty state → fresh start, advisory (never
    # raises, never hard-blocks).
    out = apply_burn_in(rt, _cfg(60), L4BurnInStore(api, "acme", "governance"), now=T0)
    assert out.active is False and _severity(rt) == "P2"


def test_validator_not_loaded_is_noop():
    # If validator_agent_registry is not in the runtime (disabled / no registry),
    # there is nothing to promote.
    api = FakeAPI(main_head=HEAD)
    store = _store(api)
    rt = SimpleNamespace(validators=[], severity_overrides={})
    out = apply_burn_in(rt, _cfg(60), store, now=T0)
    assert out.active is False and out.just_promoted is False
    assert api.bot_state_writes == []


# -- end-to-end through main() -------------------------------------------------


class _FakeAuth:
    def installations(self):
        return [{"id": 1, "account": {"login": "acme"}}]

    def installation_token(self, installation_id):
        return "fake-install-token-xyz"

    def app_slug(self):
        return "acme-merge-gate"


def _write_config(cfg_dir: Path, *, days: int, overrides: str = "") -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "owner.yml").write_text("github_login: acme\n", encoding="utf-8")
    (cfg_dir / "projects.yml").write_text(
        "governance_repo: acme/governance\nsupervised_repos:\n  - acme/governance\n",
        encoding="utf-8",
    )
    (cfg_dir / "env.yml").write_text(
        f"bot_app_slug: acme-merge-gate\nallow_no_ci: true\nl4_burn_in_days: {days}\n",
        encoding="utf-8",
    )
    # A registry so validator_agent_registry is actually loaded into the runtime.
    (cfg_dir / "agent_registry.yml").write_text(
        "tools:\n  - claude-code\nmodels:\n  claude-code: [\"*\"]\n", encoding="utf-8")
    if overrides:
        (cfg_dir / "skills.yml").write_text(overrides, encoding="utf-8")


def _run_main(tmp_path, monkeypatch, fake_api, *, days, now, overrides=""):
    _write_config(tmp_path / "config", days=days, overrides=overrides)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MERGE_GATE_APP_ID", "123")
    monkeypatch.setenv("MERGE_GATE_PRIVATE_KEY", "dummy-pem")
    monkeypatch.setattr(main_mod.AppAuth, "from_env",
                        classmethod(lambda cls, *a, **k: _FakeAuth()))
    monkeypatch.setattr(main_mod, "GitHubAPI", lambda auth, inst_id: fake_api)
    return main_mod.main([], now=now)


def test_main_burn_in_off_writes_no_clock(tmp_path, monkeypatch):
    fake_api = FakeAPI(main_head=HEAD)
    fake_api.seed_bot_state("acme", "governance", {
        "acme/governance": HEAD, "acme/governance:l2": HEAD})
    rc = _run_main(tmp_path, monkeypatch, fake_api, days=0, now=T0)
    assert rc == 0
    # No l4_burn_in.json write among the durable writes.
    assert all(w[3] != L4_BURN_IN_PATH for w in fake_api.bot_state_writes)


def test_main_burn_in_on_writes_clock_and_promotes_after_threshold(tmp_path, monkeypatch):
    # Tick 1 (T0): clock starts, advisory. Tick 2 (T0+61d): promoted.
    fake_api = FakeAPI(main_head=HEAD)
    fake_api.seed_bot_state("acme", "governance", {
        "acme/governance": HEAD, "acme/governance:l2": HEAD})

    assert _run_main(tmp_path, monkeypatch, fake_api, days=60, now=T0) == 0
    clock = fake_api._ref_files.get(
        ("acme", "governance", "bot-state", L4_BURN_IN_PATH))
    assert clock is not None
    assert json.loads(clock[0])["advisory_started_at"] == "2026-06-01T00:00:00Z"

    # Tick 2 past the threshold → promoted_at recorded.
    assert _run_main(tmp_path, monkeypatch, fake_api, days=60,
                     now=T0 + timedelta(days=61)) == 0
    clock2 = json.loads(fake_api._ref_files[
        ("acme", "governance", "bot-state", L4_BURN_IN_PATH)][0])
    assert "promoted_at" in clock2


def test_main_explicit_override_keeps_clock_inert(tmp_path, monkeypatch):
    fake_api = FakeAPI(main_head=HEAD)
    fake_api.seed_bot_state("acme", "governance", {
        "acme/governance": HEAD, "acme/governance:l2": HEAD})
    rc = _run_main(
        tmp_path, monkeypatch, fake_api, days=60, now=T0 + timedelta(days=365),
        overrides="severity_overrides:\n  validator_agent_registry: P0\n",
    )
    assert rc == 0
    # Operator pinned P0 → the clock file is never written.
    assert all(w[3] != L4_BURN_IN_PATH for w in fake_api.bot_state_writes)
