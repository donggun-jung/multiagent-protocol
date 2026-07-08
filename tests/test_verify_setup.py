"""Tests for the ``verify-setup`` audit (C2) + gate-liveness check (C4).

Uses a duck-typed fake probe/client (the repo's FakeAPI pattern) so the setup
audit is exercised end-to-end WITHOUT a network. All fixtures are synthetic
(``octo-owner`` etc.) — no real login/email/secret enters the repo, so the
personal-data CI scan never trips.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from multiagent_protocol.verify_setup import (
    liveness_status,
    parse_cron_cadence_minutes,
    run_verification,
    scan_placeholders,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
NOW = 1_800_000_000.0  # fixed clock (epoch seconds)

WORKFLOW_TEXT = (
    "name: bot-cron\n"
    "on:\n"
    "  schedule:\n"
    '    - cron: "*/30 * * * *"\n'
    "  workflow_dispatch: {}\n"
)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClient:
    """Read-only stand-in for a GitHubAPI client scoped to one App installation."""

    def __init__(self) -> None:
        self.repos: dict[tuple[str, str], dict | None] = {}
        self.labels: dict[tuple[str, str], list[str]] = {}
        self.files: dict[tuple[str, str, str], str] = {}
        self.workflows: dict[tuple[str, str, str], dict] = {}
        self.runs: dict[tuple[str, str, str], list[dict]] = {}
        self.refs: dict[tuple[str, str, str], str] = {}

    def get_repo(self, owner, repo):
        return self.repos.get((owner, repo))

    def list_labels(self, owner, repo):
        return [{"name": n} for n in self.labels.get((owner, repo), [])]

    def get_file_text(self, owner, repo, path, ref="main"):
        return self.files.get((owner, repo, path))

    def get_workflow(self, owner, repo, wf):
        return self.workflows.get((owner, repo, wf))

    def list_workflow_runs(self, owner, repo, wf, *, per_page=1, status=None, max_pages=1):
        return list(self.runs.get((owner, repo, wf), []))[:per_page]

    def get_ref_sha(self, owner, repo, ref):
        return self.refs.get((owner, repo, ref))


class _FakeProbe:
    def __init__(self, clients: dict[str, _FakeClient]) -> None:
        self._clients = clients
        self.accounts = set(clients)

    def client_for(self, owner):
        return self._clients.get(owner)


def _healthy_client() -> _FakeClient:
    c = _FakeClient()
    c.repos[("octo-owner", "gov")] = {"default_branch": "main", "allow_squash_merge": True}
    c.repos[("octo-owner", "app")] = {"default_branch": "main", "allow_squash_merge": True}
    c.labels[("octo-owner", "app")] = ["ready-to-merge", "bug"]
    c.labels[("octo-owner", "gov")] = ["decision:pending-owner", "ready-to-merge"]
    c.files[("octo-owner", "gov", ".github/workflows/bot-cron.yml")] = WORKFLOW_TEXT
    c.files[("octo-owner", "app", "AGENTS.md")] = "# Agent Rules — app\nfilled, no markers\n"
    c.workflows[("octo-owner", "gov", "bot-cron.yml")] = {"state": "active"}
    c.runs[("octo-owner", "gov", "bot-cron.yml")] = [{"created_at": _iso(NOW - 600)}]
    c.refs[("octo-owner", "gov", "bot-state")] = "a" * 40
    return c


def _healthy_probe() -> tuple[_FakeProbe, _FakeClient]:
    c = _healthy_client()
    return _FakeProbe({"octo-owner": c}), c


# ---------------------------------------------------------------------------
# Config fixtures (synthetic, filled)
# ---------------------------------------------------------------------------

_OWNER = "github_login: octo-owner\nallowlisted_actors:\n  - octo-owner\n"
_PROJECTS = "governance_repo: octo-owner/gov\nsupervised_repos:\n  - octo-owner/app\n"
_ENV = "runner_tier: actions-free\nbot_app_slug: octo-gate-bot\nallow_no_ci: true\n"
_PREFS = "language:\n  primary: en\n"
_REGISTRY = "tools:\n  - claude-code\n  - codex\n  - manual\nmodels:\n  claude-code:\n    - '*'\n"


def _write_config(tmp_path: Path, **override: str) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    files = {
        "owner.yml": _OWNER,
        "projects.yml": _PROJECTS,
        "env.yml": _ENV,
        "preferences.yml": _PREFS,
        "agent_registry.yml": _REGISTRY,
    }
    files.update(override)
    for name, text in files.items():
        if text is None:  # allow removing a file via override=None
            continue
        (cfg / name).write_text(text, encoding="utf-8")
    return cfg


def _by_id(report):
    return {c.id: c for c in report.checks}


# ---------------------------------------------------------------------------
# parse_cron_cadence_minutes (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ('- cron: "*/30 * * * *"', 30),
        ("- cron: '*/5 * * * *'", 5),
        ("- cron: */15 * * * *", 15),
        ('- cron: "0 * * * *"', 60),        # hourly at :00
        ('- cron: "0 */6 * * *"', 360),     # every 6 hours
        ('- cron: "30 3 * * *"', 1440),     # daily
        ('- cron: "5,25 * * * *"', None),   # comma list — not inferable
        ("no cron here", None),
    ],
)
def test_parse_cron_cadence(text, expected):
    assert parse_cron_cadence_minutes(text) == expected


# ---------------------------------------------------------------------------
# scan_placeholders (pure)
# ---------------------------------------------------------------------------


def test_scan_placeholders_clean(tmp_path):
    cfg = _write_config(tmp_path)
    assert scan_placeholders(cfg) == []


def test_scan_placeholders_flags_your_and_mustache(tmp_path):
    cfg = _write_config(
        tmp_path,
        **{
            "owner.yml": "github_login: your-github-login\nallowlisted_actors:\n  - your-github-login\n",
            "env.yml": "runner_tier: actions-free\nbot_app_slug: your-merge-gate-bot\n",
            "projects.yml": "governance_repo: octo-owner/{{REPO}}\nsupervised_repos:\n  - octo-owner/app\n",
        },
    )
    findings = scan_placeholders(cfg)
    joined = " ".join(findings)
    assert "your-github-login" in joined
    assert "your-merge-gate-bot" in joined
    assert "{{REPO}}" in joined


def test_scan_placeholders_allows_legit_freetext(tmp_path):
    # An angle-bracketed phrase with no placeholder keyword must NOT trip.
    cfg = _write_config(
        tmp_path,
        **{"owner.yml": "github_login: octo-owner\ndisplay_name: R&D <team lead>\n"},
    )
    assert scan_placeholders(cfg) == []


# ---------------------------------------------------------------------------
# liveness_status (pure) — C4 threshold behavior
# ---------------------------------------------------------------------------


def test_liveness_fresh_passes():
    c = liveness_status(_iso(NOW - 600), 30, now=NOW)
    assert c.status == "PASS" and "GATE LIVE" in c.detail


def test_liveness_stale_warns_not_fails_on_plain_run():
    # 5 days old, cadence 30m → way past 2×; a PLAIN re-run must WARN, never FAIL
    # (documented cron lag would otherwise create false reds).
    c = liveness_status(_iso(NOW - 5 * 24 * 3600), 30, now=NOW)
    assert c.status == "WARN" and "GATE MAY BE DOWN" in c.detail


def test_liveness_stale_fails_only_in_e2e():
    c = liveness_status(_iso(NOW - 5 * 24 * 3600), 30, now=NOW, e2e=True)
    assert c.status == "FAIL" and "GATE MAY BE DOWN" in c.detail


def test_liveness_no_runs_warns():
    c = liveness_status(None, 30, now=NOW)
    assert c.status == "WARN" and "no bot-cron runs" in c.detail


def test_liveness_no_runs_fails_in_e2e():
    c = liveness_status(None, 30, now=NOW, e2e=True)
    assert c.status == "FAIL"


def test_liveness_unknown_cadence_warns():
    c = liveness_status(_iso(NOW - 600), None, now=NOW)
    assert c.status == "WARN" and "cannot infer cadence" in c.detail


# ---------------------------------------------------------------------------
# run_verification — full report
# ---------------------------------------------------------------------------


def test_healthy_deployment_all_green(tmp_path):
    cfg = _write_config(tmp_path)
    probe, _ = _healthy_probe()
    report = run_verification(
        config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW
    )
    by = _by_id(report)
    assert report.ok is True
    assert report.summary["failed"] == 0
    for cid in (
        "config-loads", "preferences-schema", "config-placeholders",
        "app-installed", "workflow-file", "bot-cron-enabled", "gate-liveness",
        "ready-to-merge-label", "squash-allowed", "bot-state-branch",
        "adopter-kit-markers",
    ):
        assert by[cid].status == "PASS", (cid, by[cid].detail)
    assert by["app-auth"].status == "PASS"
    assert by["secrets-present"].status == "SKIP"
    assert by["decision-labels"].status == "INFO"
    assert by["merge-mode"].status == "INFO"
    assert by["allowlist-actors"].status == "INFO"


def test_no_credentials_skips_github_but_local_passes(tmp_path):
    cfg = _write_config(tmp_path)
    report = run_verification(
        config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=None, now=NOW
    )
    by = _by_id(report)
    assert report.ok is True  # SKIP is not FAIL — useful pre-secrets
    assert by["app-auth"].status == "SKIP"
    assert by["config-loads"].status == "PASS"
    assert by["config-placeholders"].status == "PASS"
    for cid in ("app-installed", "workflow-file", "gate-liveness", "squash-allowed"):
        assert by[cid].status == "SKIP"


def test_placeholder_config_fails(tmp_path):
    cfg = _write_config(
        tmp_path,
        **{"owner.yml": "github_login: your-github-login\nallowlisted_actors:\n  - your-github-login\n"},
    )
    probe, _ = _healthy_probe()
    report = run_verification(
        config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW
    )
    by = _by_id(report)
    assert by["config-placeholders"].status == "FAIL"
    assert report.ok is False


def test_squash_disabled_fails(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    client.repos[("octo-owner", "app")] = {"default_branch": "main", "allow_squash_merge": False}
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["squash-allowed"].status == "FAIL"
    assert report.ok is False


def test_missing_ready_to_merge_label_fails(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    client.labels[("octo-owner", "app")] = ["bug"]
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["ready-to-merge-label"].status == "FAIL"
    assert report.ok is False


def test_app_not_covering_supervised_repo_fails(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    client.repos[("octo-owner", "app")] = None  # installed on account, repo not granted
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["app-installed"].status == "FAIL"
    assert "app" in by["app-installed"].detail
    assert report.ok is False


def test_missing_workflow_file_fails(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    del client.files[("octo-owner", "gov", ".github/workflows/bot-cron.yml")]
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["workflow-file"].status == "FAIL"
    assert report.ok is False


def test_disabled_bot_cron_fails(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    client.workflows[("octo-owner", "gov", "bot-cron.yml")] = {"state": "disabled_inactivity"}
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["bot-cron-enabled"].status == "FAIL"
    assert "disabled_inactivity" in by["bot-cron-enabled"].detail
    assert report.ok is False


def test_stale_tick_warns_but_report_stays_ok(tmp_path):
    # A silently-dead-but-not-disabled cron: workflow active, last run ancient.
    # gate-liveness WARNs; the plain report must NOT hard-fail on it.
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    client.runs[("octo-owner", "gov", "bot-cron.yml")] = [{"created_at": _iso(NOW - 10 * 24 * 3600)}]
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["gate-liveness"].status == "WARN"
    assert report.ok is True


def test_stale_tick_fails_in_e2e_mode(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    client.runs[("octo-owner", "gov", "bot-cron.yml")] = [{"created_at": _iso(NOW - 10 * 24 * 3600)}]
    report = run_verification(
        config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW, e2e=True
    )
    assert _by_id(report)["gate-liveness"].status == "FAIL"
    assert report.ok is False


def test_bot_state_branch_absent_warns_not_fails(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    del client.refs[("octo-owner", "gov", "bot-state")]
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["bot-state-branch"].status == "WARN"
    assert report.ok is True


def test_adopter_kit_unfilled_markers_fail(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    client.files[("octo-owner", "app", "AGENTS.md")] = "# Agent Rules — {{REPO_NAME}}\n"
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["adopter-kit-markers"].status == "FAIL"
    assert report.ok is False


def test_adopter_kit_absent_warns(tmp_path):
    cfg = _write_config(tmp_path)
    probe, client = _healthy_probe()
    del client.files[("octo-owner", "app", "AGENTS.md")]
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["adopter-kit-markers"].status == "WARN"
    assert report.ok is True


def test_allowlist_login_present_passes(tmp_path):
    cfg = _write_config(tmp_path)
    probe, _ = _healthy_probe()
    report = run_verification(
        config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe,
        operator_login="octo-owner", now=NOW,
    )
    assert _by_id(report)["allowlist-actors"].status == "PASS"


def test_allowlist_login_absent_fails(tmp_path):
    cfg = _write_config(tmp_path)
    probe, _ = _healthy_probe()
    report = run_verification(
        config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe,
        operator_login="not-the-owner", now=NOW,
    )
    by = _by_id(report)
    assert by["allowlist-actors"].status == "FAIL"
    assert report.ok is False


def test_merge_mode_live_vs_observe(tmp_path):
    cfg = _write_config(tmp_path)
    probe, _ = _healthy_probe()
    live = run_verification(
        config_dir=cfg, schemas_dir=SCHEMAS, env={"MERGE_GATE_MERGE_ENABLED": "true"},
        probe=probe, now=NOW,
    )
    observe = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    assert "LIVE" in _by_id(live)["merge-mode"].detail
    assert "OBSERVE" in _by_id(observe)["merge-mode"].detail


def test_invalid_preferences_fails(tmp_path):
    cfg = _write_config(tmp_path, **{"preferences.yml": "language:\n  reports: primary\n"})  # missing required primary
    probe, _ = _healthy_probe()
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["preferences-schema"].status == "FAIL"
    assert report.ok is False


def test_config_does_not_load_fails_and_skips_github(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "owner.yml").write_text("github_login: octo-owner\n", encoding="utf-8")
    # projects.yml + env.yml missing → load_config raises.
    probe, _ = _healthy_probe()
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    by = _by_id(report)
    assert by["config-loads"].status == "FAIL"
    assert by["app-installed"].status == "SKIP"
    assert report.ok is False


# ---------------------------------------------------------------------------
# JSON shape + status line
# ---------------------------------------------------------------------------


def test_json_report_shape(tmp_path):
    import json

    cfg = _write_config(tmp_path)
    probe, _ = _healthy_probe()
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    data = json.loads(report.to_json())
    assert set(data) == {"ok", "status", "summary", "checks"}
    assert set(data["summary"]) == {"passed", "failed", "warnings", "skipped", "info"}
    assert all(set(c) == {"id", "status", "detail"} for c in data["checks"])
    assert data["ok"] is True
    assert data["status"].startswith("SETUP: OK")


def test_status_line_reflects_failure(tmp_path):
    cfg = _write_config(
        tmp_path,
        **{"owner.yml": "github_login: your-github-login\n"},
    )
    probe, _ = _healthy_probe()
    report = run_verification(config_dir=cfg, schemas_dir=SCHEMAS, env={}, probe=probe, now=NOW)
    assert report.status_line.startswith("SETUP: FAIL")
    assert report.summary["failed"] >= 1


# ---------------------------------------------------------------------------
# CLI wiring (real os.environ path, no network)
# ---------------------------------------------------------------------------


def test_cli_verify_setup_clean_exits_zero(tmp_path, monkeypatch):
    from multiagent_protocol.cli import main

    monkeypatch.delenv("MERGE_GATE_APP_ID", raising=False)
    monkeypatch.delenv("MERGE_GATE_PRIVATE_KEY", raising=False)
    cfg = _write_config(tmp_path)
    rc = main([
        "verify-setup", "--config-dir", str(cfg), "--schemas-dir", str(SCHEMAS),
    ])
    assert rc == 0


def test_cli_verify_setup_placeholder_exits_one(tmp_path, monkeypatch, capsys):
    from multiagent_protocol.cli import main

    monkeypatch.delenv("MERGE_GATE_APP_ID", raising=False)
    monkeypatch.delenv("MERGE_GATE_PRIVATE_KEY", raising=False)
    cfg = _write_config(
        tmp_path, **{"owner.yml": "github_login: your-github-login\n"}
    )
    rc = main([
        "verify-setup", "--config-dir", str(cfg), "--schemas-dir", str(SCHEMAS), "--json",
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert '"ok": false' in out


def test_cli_verify_setup_json_flag(tmp_path, monkeypatch, capsys):
    from multiagent_protocol.cli import main

    monkeypatch.delenv("MERGE_GATE_APP_ID", raising=False)
    monkeypatch.delenv("MERGE_GATE_PRIVATE_KEY", raising=False)
    cfg = _write_config(tmp_path)
    main([
        "verify-setup", "--config-dir", str(cfg), "--schemas-dir", str(SCHEMAS), "--json",
    ])
    out = capsys.readouterr().out
    assert '"summary"' in out and '"checks"' in out
