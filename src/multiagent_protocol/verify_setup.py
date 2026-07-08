"""Setup verification — re-check a DEPLOYED installation against GitHub.

``check-config`` validates the LOCAL config files. This module goes one step
further and re-verifies the gate that is actually deployed on GitHub: the App
installation coverage, the workflow file, the ``ready-to-merge`` label, squash
merging, the bot-state branch, and — the trust-critical one — that the cron is
still *ticking* (GitHub silently auto-disables a schedule after ~60 days, and a
dead cron leaves a green history behind it).

It is deliberately **read-only** against GitHub and **serverless**: it runs
from the operator's machine or as an Actions job using the same
``MERGE_GATE_*`` App credentials the tick uses. Every GitHub-dependent check
degrades to ``SKIP (no credentials)`` when the App creds are absent, so the
command is still useful *before* secrets are wired.

What it produces is a **setup verification report** — NOT a "receipt" (that word
is the C3 HMAC approval receipt / ``MERGE_GATE_RECEIPT_KEY``) and NOT a
"diagnostic report" (that is the L1 block comment). Structure:
``summary{passed,failed,warnings,skipped,info}`` + ``checks[{id,status,detail}]``
+ one machine-readable STATUS line. The report EXITS NON-ZERO on any FAIL.

Honest scope (this is a trust product): the report verifies ARTIFACT PRESENCE
and BOT LIVENESS — that the pieces are in place and the cron is running. It does
NOT prove the gate is functionally correct. Secret *values* are unreadable by
design; the live end-to-end rehearsal (AGENT_SETUP Step 9) is what proves the
PEM actually merges. A PASS means "setup artifacts present and the bot is
ticking," and points at Step 9 for the functional proof.

The report contains repo names / an App slug, so — like every operator surface
— it must go to stdout or an Actions artifact, NEVER be committed to the public
upstream. The public repo ships only synthetic fixtures for the tests.
"""

from __future__ import annotations

import json as _json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

logger_status = Literal["PASS", "FAIL", "WARN", "SKIP", "INFO"]

# The three Actions secrets the deployed workflow consumes. Their *presence* can
# only be confirmed with the operator's own token (``gh secret list``), never
# with the App token — so the setup audit reports them as SKIP + guidance rather
# than faking a green.
REQUIRED_SECRET_NAMES = (
    "MERGE_GATE_APP_ID",
    "MERGE_GATE_PRIVATE_KEY",
    "MERGE_GATE_RECEIPT_KEY",
)

BOT_CRON_WORKFLOW_FILE = "bot-cron.yml"
BOT_CRON_WORKFLOW_PATH = ".github/workflows/bot-cron.yml"
BOT_STATE_BRANCH = "bot-state"
READY_TO_MERGE_LABEL = "ready-to-merge"

# Config files scanned for unfilled placeholders (string leaves only).
_CONFIG_FILES = (
    "owner.yml",
    "projects.yml",
    "env.yml",
    "skills.yml",
    "agent_registry.yml",
    "preferences.yml",
)

# High-confidence "you didn't fill this in" tokens. Scoped to string leaves and
# kept tight so legitimate free-text (display names, taste-ledger prose) does
# not false-fail. ``<...>`` only trips when it wraps a placeholder-ish keyword,
# never on an ordinary bracketed phrase.
_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\{\{[^}]+\}\}"),  # {{TOKEN}} mustache marker
    re.compile(r"\byour-[a-z0-9-]+\b", re.IGNORECASE),  # your-github-login, your-merge-gate-bot
    re.compile(
        r"<[^>]*(?:your|here|todo|fixme|changeme|placeholder|xxx|slug|handle)[^>]*>",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:CHANGE[_-]?ME|REPLACE[_-]?ME|FILL[_-]?ME[_-]?IN)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class Check:
    """One line of the setup verification report."""

    id: str
    status: logger_status
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class VerificationReport:
    """The structured setup-verification result (summary + per-check lines)."""

    checks: tuple[Check, ...]

    def _count(self, status: str) -> int:
        return sum(1 for c in self.checks if c.status == status)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "passed": self._count("PASS"),
            "failed": self._count("FAIL"),
            "warnings": self._count("WARN"),
            "skipped": self._count("SKIP"),
            "info": self._count("INFO"),
        }

    @property
    def ok(self) -> bool:
        """True iff no check FAILed. This drives the process exit code."""
        return self._count("FAIL") == 0

    @property
    def status_line(self) -> str:
        s = self.summary
        verdict = "OK" if self.ok else "FAIL"
        return (
            f"SETUP: {verdict} — passed={s['passed']} failed={s['failed']} "
            f"warnings={s['warnings']} skipped={s['skipped']} info={s['info']}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status_line,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
        }

    def to_json(self) -> str:
        return _json.dumps(self.to_dict(), indent=2)

    def render_table(self) -> str:
        """Human-readable table. Widths are content-driven, single STATUS line."""
        width = max((len(c.id) for c in self.checks), default=4)
        lines = ["setup verification report", ""]
        for c in self.checks:
            lines.append(f"[{c.status:<4}] {c.id.ljust(width)}  {c.detail}")
        lines.append("")
        lines.append(self.status_line)
        if not self.ok:
            lines.append(
                "One or more CRITICAL artifacts are missing — see the FAIL rows above."
            )
        lines.append(
            "Scope: this confirms artifacts are present and the bot is ticking, "
            "not that the gate is functionally correct — run AGENT_SETUP Step 9 "
            "(live rehearsal) for that proof."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested directly).
# ---------------------------------------------------------------------------

_CRON_RE = re.compile(r"cron:\s*['\"]?\s*([^'\"\n#]+?)\s*['\"]?\s*(?:#.*)?$", re.MULTILINE)


def parse_cron_cadence_minutes(workflow_text: str) -> int | None:
    """Infer the schedule cadence (minutes) from a workflow's first ``cron:`` line.

    Handles the cadences the deploy example documents:
    ``*/N * * * *`` → N, ``0 * * * *`` (hourly) → 60, ``*/N`` in the hour field
    → N*60, a fixed daily minute+hour → 1440. Anything more exotic (comma lists,
    ranges, step in >1 field) returns None so the caller WARNs "cannot infer
    cadence" instead of guessing.
    """
    m = _CRON_RE.search(workflow_text)
    if not m:
        return None
    fields = m.group(1).split()
    if len(fields) < 5:
        return None
    minute, hour = fields[0], fields[1]
    step = re.fullmatch(r"\*/(\d+)", minute)
    if step:
        n = int(step.group(1))
        return n if n > 0 else None
    if minute == "*":
        return 1
    if re.fullmatch(r"\d+", minute):
        hour_step = re.fullmatch(r"\*/(\d+)", hour)
        if hour_step:
            n = int(hour_step.group(1))
            return n * 60 if n > 0 else None
        if hour == "*":
            return 60
        if re.fullmatch(r"\d+", hour):
            return 24 * 60
    return None


def _iter_string_leaves(node: Any, path: str = "") -> Any:
    """Yield ``(json_pointer, string)`` for every string key/value in a YAML tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            key_path = f"{path}.{k}" if path else str(k)
            if isinstance(k, str):
                yield key_path, k
            yield from _iter_string_leaves(v, key_path)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_string_leaves(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def scan_placeholders(config_dir: Path) -> list[str]:
    """Return ``file: pointer = 'token'`` findings for unfilled placeholders.

    Scans only string leaves of each present config file. Empty list = clean.
    """
    findings: list[str] = []
    for name in _CONFIG_FILES:
        p = config_dir / name
        if not p.exists():
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue  # config-loads owns malformed-YAML reporting
        for pointer, text in _iter_string_leaves(data):
            for pat in _PLACEHOLDER_PATTERNS:
                hit = pat.search(text)
                if hit:
                    findings.append(f"{name}: {pointer or '<root>'} = {hit.group(0)!r}")
                    break
    return findings


def liveness_status(
    last_run_iso: str | None,
    cadence_minutes: int | None,
    *,
    now: float,
    e2e: bool = False,
) -> Check:
    """C4 gate-liveness verdict from the latest bot-cron run + cadence.

    Pull-based only — this reads the run's timestamp, it never writes. A plain
    re-run WARNs on a stale/absent tick (documented GitHub cron lag would make a
    hard-fail a false red and drag the tool toward uptime monitoring). Only the
    Step-9 go-live / ``--e2e`` mode, where a tick was just dispatched on demand,
    hard-FAILs a missing/stale run.
    """
    stale_status: logger_status = "FAIL" if e2e else "WARN"
    if last_run_iso is None:
        return Check(
            "gate-liveness",
            stale_status,
            "no bot-cron runs found yet (pre-first-tick, or the schedule has "
            "never fired). Dispatch one: `gh workflow run bot-cron.yml`.",
        )
    try:
        last = datetime.strptime(last_run_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return Check("gate-liveness", "WARN", f"could not parse last-run time {last_run_iso!r}.")
    age_min = (now - last.timestamp()) / 60.0
    age_txt = _humanize_minutes(age_min)
    if cadence_minutes is None:
        return Check(
            "gate-liveness",
            "WARN",
            f"last tick {age_txt} ago, but cannot infer cadence from the workflow "
            "cron line — verify the schedule manually.",
        )
    threshold = 2 * cadence_minutes
    if age_min > threshold:
        return Check(
            "gate-liveness",
            stale_status,
            f"GATE MAY BE DOWN — last tick {age_txt} ago > 2× cadence "
            f"({cadence_minutes}m). Re-enable: `gh workflow enable bot-cron.yml`, "
            "then `gh workflow run bot-cron.yml`.",
        )
    return Check(
        "gate-liveness",
        "PASS",
        f"GATE LIVE — last tick {age_txt} ago (cadence {cadence_minutes}m).",
    )


def _humanize_minutes(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f}m"
    if minutes < 60 * 24:
        return f"{minutes / 60:.1f}h"
    return f"{minutes / (60 * 24):.1f}d"


# ---------------------------------------------------------------------------
# Live GitHub probe (App auth → per-account read-only client).
# ---------------------------------------------------------------------------


class GitHubProbe:
    """Maps a repo owner to the read-only client for the App installation on it.

    Built from ``AppAuth.installations()`` — the same discovery the tick uses.
    Tests inject a duck-typed stand-in with the same ``accounts`` /
    ``client_for`` surface, so the setup audit is itself testable without a
    network.
    """

    def __init__(self, auth: Any) -> None:
        from multiagent_protocol.github_api import GitHubAPI

        self._auth = auth
        self.accounts: set[str] = set()
        self._clients: dict[str, Any] = {}
        for inst in auth.installations():
            login = (inst.get("account") or {}).get("login")
            iid = inst.get("id")
            if login and iid is not None:
                self.accounts.add(login)
                self._clients[login] = GitHubAPI(auth, iid)

    def client_for(self, owner: str) -> Any | None:
        return self._clients.get(owner)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GitHubProbe:
        from multiagent_protocol.auth import AppAuth

        return cls(AppAuth.from_env(dict(env)))


def _has_app_creds(env: Mapping[str, str]) -> bool:
    return bool(env.get("MERGE_GATE_APP_ID") and env.get("MERGE_GATE_PRIVATE_KEY"))


# ---------------------------------------------------------------------------
# The audit.
# ---------------------------------------------------------------------------


def run_verification(
    *,
    config_dir: Path,
    schemas_dir: Path | None,
    env: Mapping[str, str],
    probe: Any | None = None,
    operator_login: str | None = None,
    e2e: bool = False,
    now: float | None = None,
) -> VerificationReport:
    """Run every setup check and return the structured report.

    ``probe`` is injectable for tests; in production it is built from the App
    credentials in ``env``. When no credentials are present (and no probe is
    injected), every GitHub-dependent check degrades to ``SKIP (no
    credentials)`` so the command is still useful pre-secrets.
    """
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    checks: list[Check] = []

    # -- Local checks (no GitHub) ------------------------------------------
    config = _check_config_loads(config_dir, schemas_dir, checks)
    _check_preferences_schema(config_dir, schemas_dir, checks)
    _check_placeholders(config_dir, checks)
    _check_allowlist(config, operator_login, checks)
    _check_agent_tools(config, checks)
    _check_merge_mode(env, checks)

    # -- Secrets: can only be inferred with the operator's own token -------
    gov = config.projects.governance_repo if config else "<governance-repo>"
    checks.append(
        Check(
            "secrets-present",
            "SKIP",
            "cannot be read via the App token (by design). Confirm with your own "
            f"login: `gh secret list -R {gov}` — expect {', '.join(REQUIRED_SECRET_NAMES)}.",
        )
    )

    # -- Build the probe / app-auth ----------------------------------------
    creds_present = probe is not None or _has_app_creds(env)
    if probe is None and _has_app_creds(env):
        try:
            probe = GitHubProbe.from_env(env)
        except Exception as e:  # noqa: BLE001 — surface auth failure as a check
            checks.append(
                Check("app-auth", "FAIL", f"App authentication failed: {e}")
            )
            probe = None
            creds_present = False
    if probe is not None:
        checks.append(
            Check(
                "app-auth",
                "PASS",
                f"App authenticated; {len(getattr(probe, 'accounts', []))} "
                "installation account(s) visible.",
            )
        )
    elif not creds_present:
        checks.append(
            Check(
                "app-auth",
                "SKIP",
                "no credentials (MERGE_GATE_APP_ID / MERGE_GATE_PRIVATE_KEY unset) "
                "— GitHub-side checks skipped; local checks above still apply.",
            )
        )

    # -- GitHub-dependent checks -------------------------------------------
    if config is None or probe is None:
        reason = (
            "config did not load" if config is None else "no App credentials"
        )
        for cid in (
            "app-installed",
            "workflow-file",
            "bot-cron-enabled",
            "gate-liveness",
            "ready-to-merge-label",
            "squash-allowed",
            "bot-state-branch",
            "adopter-kit-markers",
            "decision-labels",
        ):
            checks.append(Check(cid, "SKIP", f"skipped — {reason}."))
        return VerificationReport(tuple(checks))

    gov_owner, _, gov_repo = config.projects.governance_repo.partition("/")
    supervised = list(config.projects.supervised_repos)
    inbox_owner, _, inbox_repo = (
        config.projects.effective_inbox_repository.partition("/")
    )

    _check_app_installed(probe, gov_owner, gov_repo, supervised, checks)
    cadence = _check_workflow_file(probe, gov_owner, gov_repo, checks)
    _check_bot_cron_enabled(probe, gov_owner, gov_repo, checks)
    _check_gate_liveness(probe, gov_owner, gov_repo, cadence, now, e2e, checks)
    _check_ready_to_merge(probe, supervised, checks)
    _check_squash(probe, supervised, checks)
    _check_bot_state_branch(probe, gov_owner, gov_repo, checks)
    _check_adopter_kit(probe, supervised, checks)
    _check_decision_labels(probe, inbox_owner, inbox_repo, checks)

    return VerificationReport(tuple(checks))


# -- individual checks -------------------------------------------------------


def _check_config_loads(config_dir, schemas_dir, checks):
    from multiagent_protocol.config.loader import load_config

    try:
        cfg = load_config(config_dir, schemas_dir if schemas_dir and schemas_dir.exists() else None)
    except Exception as e:  # noqa: BLE001
        checks.append(
            Check(
                "config-loads",
                "FAIL",
                f"config under {config_dir}/ did not load: {e}",
            )
        )
        return None
    checks.append(
        Check(
            "config-loads",
            "PASS",
            f"config loaded; governance={cfg.projects.governance_repo}, "
            f"supervised={len(cfg.projects.supervised_repos)}, "
            f"runner_tier={cfg.env.runner_tier}.",
        )
    )
    return cfg


def _check_preferences_schema(config_dir, schemas_dir, checks):
    prefs = config_dir / "preferences.yml"
    if not prefs.exists():
        checks.append(
            Check("preferences-schema", "SKIP", "no config/preferences.yml (optional).")
        )
        return
    schema_path = (schemas_dir / "preferences.schema.json") if schemas_dir else None
    if schema_path is None or not schema_path.exists():
        checks.append(
            Check("preferences-schema", "SKIP", "preferences.schema.json not available.")
        )
        return
    from jsonschema import validate as jsonschema_validate

    try:
        data = yaml.safe_load(prefs.read_text(encoding="utf-8")) or {}
        jsonschema_validate(instance=data, schema=_json.loads(schema_path.read_text()))
    except Exception as e:  # noqa: BLE001
        checks.append(
            Check("preferences-schema", "FAIL", f"preferences.yml fails its schema: {e}")
        )
        return
    checks.append(
        Check("preferences-schema", "PASS", "preferences.yml validates against its schema.")
    )


def _check_placeholders(config_dir, checks):
    findings = scan_placeholders(config_dir)
    if findings:
        shown = "; ".join(findings[:8])
        more = f" (+{len(findings) - 8} more)" if len(findings) > 8 else ""
        checks.append(
            Check(
                "config-placeholders",
                "FAIL",
                f"unfilled placeholder(s) in config: {shown}{more}. Replace them "
                "with your real values.",
            )
        )
    else:
        checks.append(
            Check("config-placeholders", "PASS", "no unfilled placeholders in config files.")
        )


def _check_allowlist(config, operator_login, checks):
    if config is None:
        checks.append(Check("allowlist-actors", "SKIP", "config did not load."))
        return
    actors = config.owner.allowlisted_actors
    listed = ", ".join(actors)
    if operator_login is not None:
        if operator_login in actors:
            checks.append(
                Check(
                    "allowlist-actors",
                    "PASS",
                    f"your login {operator_login!r} is allowlisted "
                    f"(allowlisted_actors: {listed}).",
                )
            )
        else:
            checks.append(
                Check(
                    "allowlist-actors",
                    "FAIL",
                    f"your login {operator_login!r} is NOT in allowlisted_actors "
                    f"({listed}). The account that applies '{READY_TO_MERGE_LABEL}' / "
                    "answers Decision-Inbox issues MUST be allowlisted, or the gate "
                    "silently ignores it (the documented #1 C1 failure).",
                )
            )
        return
    checks.append(
        Check(
            "allowlist-actors",
            "INFO",
            f"allowlisted_actors: {listed}. Confirm YOUR GitHub login is in this "
            f"list — the account that applies '{READY_TO_MERGE_LABEL}' and answers "
            "Decision-Inbox issues must be allowlisted (re-run with --login <you> "
            "to assert this).",
        )
    )


def _check_agent_tools(config, checks):
    if config is None:
        checks.append(Check("agent-tools-declared", "SKIP", "config did not load."))
        return
    registry = config.agent_registry
    if registry is None or not registry.tools:
        checks.append(
            Check(
                "agent-tools-declared",
                "INFO",
                "no config/agent_registry.yml tools declared — the L4 identity gate "
                "will accept trailers advisorily. Declare the agent CLIs you use.",
            )
        )
        return
    tools = ", ".join(registry.tools)
    checks.append(
        Check(
            "agent-tools-declared",
            "INFO",
            f"declared agent tools (vendor-neutral, from your registry): {tools}. "
            "Ensure each corresponding CLI is installed where your agents run.",
        )
    )


def _check_merge_mode(env, checks):
    live = str(env.get("MERGE_GATE_MERGE_ENABLED", "")).strip().lower() == "true"
    if live:
        checks.append(
            Check(
                "merge-mode",
                "INFO",
                "LIVE — MERGE_GATE_MERGE_ENABLED=true; the gate merges passing PRs.",
            )
        )
    else:
        checks.append(
            Check(
                "merge-mode",
                "INFO",
                "OBSERVE — MERGE_GATE_MERGE_ENABLED is not 'true'; the gate evaluates "
                "and routes but does NOT merge. This is the safe default; set the "
                "variable to go live once Step 9 passes.",
            )
        )


def _check_app_installed(probe, gov_owner, gov_repo, supervised, checks):
    targets = [f"{gov_owner}/{gov_repo}", *supervised]
    not_covered: list[str] = []
    for full in targets:
        owner, _, name = full.partition("/")
        if owner not in getattr(probe, "accounts", set()):
            not_covered.append(f"{full} (App not installed on account '{owner}')")
            continue
        client = probe.client_for(owner)
        if client is None or client.get_repo(owner, name) is None:
            not_covered.append(f"{full} (installed on '{owner}' but this repo not granted)")
    if not_covered:
        checks.append(
            Check(
                "app-installed",
                "FAIL",
                "App installation does not cover: " + "; ".join(not_covered)
                + ". Add the repo(s) to the App installation (Settings → this App "
                "→ Repository access). This is the 'tick green but supervised=0' gap.",
            )
        )
    else:
        checks.append(
            Check(
                "app-installed",
                "PASS",
                f"App installation covers governance + all {len(supervised)} "
                "supervised repo(s).",
            )
        )


def _check_workflow_file(probe, gov_owner, gov_repo, checks) -> int | None:
    client = probe.client_for(gov_owner)
    if client is None:
        checks.append(Check("workflow-file", "FAIL", f"no App client for '{gov_owner}'."))
        return None
    repo = client.get_repo(gov_owner, gov_repo)
    default_branch = (repo or {}).get("default_branch", "main")
    text = client.get_file_text(gov_owner, gov_repo, BOT_CRON_WORKFLOW_PATH, ref=default_branch)
    if text is None:
        checks.append(
            Check(
                "workflow-file",
                "FAIL",
                f"{BOT_CRON_WORKFLOW_PATH} not found on {gov_owner}/{gov_repo}@"
                f"{default_branch}. Copy deploy/bot-cron.example.yml into place "
                "(AGENT_SETUP Step 3).",
            )
        )
        return None
    cadence = parse_cron_cadence_minutes(text)
    cadence_txt = f"{cadence}m cadence" if cadence else "cadence not inferable"
    checks.append(
        Check(
            "workflow-file",
            "PASS",
            f"{BOT_CRON_WORKFLOW_PATH} present on the default branch ({cadence_txt}).",
        )
    )
    return cadence


def _check_bot_cron_enabled(probe, gov_owner, gov_repo, checks):
    client = probe.client_for(gov_owner)
    if client is None:
        checks.append(Check("bot-cron-enabled", "SKIP", f"no App client for '{gov_owner}'."))
        return
    wf = client.get_workflow(gov_owner, gov_repo, BOT_CRON_WORKFLOW_FILE)
    if wf is None:
        checks.append(
            Check(
                "bot-cron-enabled",
                "WARN",
                "bot-cron workflow not registered in the Actions API yet (it "
                "registers on first appearance on the default branch / first run).",
            )
        )
        return
    state = wf.get("state", "unknown")
    if state == "active":
        checks.append(Check("bot-cron-enabled", "PASS", "bot-cron workflow is active."))
    else:
        checks.append(
            Check(
                "bot-cron-enabled",
                "FAIL",
                f"bot-cron workflow state is '{state}' (GitHub auto-disables a "
                "schedule after ~60 days of repo inactivity). Re-enable: "
                "`gh workflow enable bot-cron.yml`.",
            )
        )


def _check_gate_liveness(probe, gov_owner, gov_repo, cadence, now, e2e, checks):
    client = probe.client_for(gov_owner)
    if client is None:
        checks.append(Check("gate-liveness", "SKIP", f"no App client for '{gov_owner}'."))
        return
    runs = client.list_workflow_runs(gov_owner, gov_repo, BOT_CRON_WORKFLOW_FILE, per_page=1)
    last_iso = runs[0].get("created_at") if runs else None
    checks.append(liveness_status(last_iso, cadence, now=now, e2e=e2e))


def _check_ready_to_merge(probe, supervised, checks):
    if not supervised:
        checks.append(Check("ready-to-merge-label", "SKIP", "no supervised repos configured."))
        return
    missing, unreadable = [], []
    for full in supervised:
        owner, _, name = full.partition("/")
        client = probe.client_for(owner)
        if client is None:
            unreadable.append(full)
            continue
        names = {lbl.get("name") for lbl in client.list_labels(owner, name)}
        if READY_TO_MERGE_LABEL not in names:
            missing.append(full)
    if missing:
        checks.append(
            Check(
                "ready-to-merge-label",
                "FAIL",
                f"'{READY_TO_MERGE_LABEL}' label missing on: {', '.join(missing)}. "
                "Create it in each supervised repo (AGENT_SETUP Step 5).",
            )
        )
    elif unreadable and len(unreadable) == len(supervised):
        checks.append(
            Check("ready-to-merge-label", "SKIP", "no supervised repo was readable.")
        )
    else:
        detail = f"'{READY_TO_MERGE_LABEL}' present on all readable supervised repos"
        if unreadable:
            detail += f" (unreadable, skipped: {', '.join(unreadable)})"
        checks.append(Check("ready-to-merge-label", "PASS", detail + "."))


def _check_squash(probe, supervised, checks):
    if not supervised:
        checks.append(Check("squash-allowed", "SKIP", "no supervised repos configured."))
        return
    disabled, unreadable = [], []
    for full in supervised:
        owner, _, name = full.partition("/")
        client = probe.client_for(owner)
        if client is None:
            unreadable.append(full)
            continue
        repo = client.get_repo(owner, name)
        if repo is None:
            unreadable.append(full)
        elif not repo.get("allow_squash_merge", False):
            disabled.append(full)
    if disabled:
        checks.append(
            Check(
                "squash-allowed",
                "FAIL",
                f"squash merging is DISABLED on: {', '.join(disabled)}. The gate "
                "merges via squash — enable 'Allow squash merging' (Settings → "
                "General → Pull Requests).",
            )
        )
    elif unreadable and len(unreadable) == len(supervised):
        checks.append(Check("squash-allowed", "SKIP", "no supervised repo was readable."))
    else:
        detail = "squash merging enabled on all readable supervised repos"
        if unreadable:
            detail += f" (unreadable, skipped: {', '.join(unreadable)})"
        checks.append(Check("squash-allowed", "PASS", detail + "."))


def _check_bot_state_branch(probe, gov_owner, gov_repo, checks):
    client = probe.client_for(gov_owner)
    if client is None:
        checks.append(Check("bot-state-branch", "SKIP", f"no App client for '{gov_owner}'."))
        return
    sha = client.get_ref_sha(gov_owner, gov_repo, BOT_STATE_BRANCH)
    if sha:
        checks.append(
            Check("bot-state-branch", "PASS", f"'{BOT_STATE_BRANCH}' branch exists.")
        )
    else:
        checks.append(
            Check(
                "bot-state-branch",
                "WARN",
                f"'{BOT_STATE_BRANCH}' branch not found — expected before the first "
                "successful tick; the tick creates it. WARN (not FAIL) pre-first-tick.",
            )
        )


def _check_adopter_kit(probe, supervised, checks):
    if not supervised:
        checks.append(Check("adopter-kit-markers", "SKIP", "no supervised repos configured."))
        return
    marker_re = re.compile(r"\{\{[^}]+\}\}")
    with_markers, absent = [], []
    for full in supervised:
        owner, _, name = full.partition("/")
        client = probe.client_for(owner)
        if client is None:
            absent.append(full)
            continue
        found_any = False
        for kit in ("AGENTS.md", "CLAUDE.md"):
            text = client.get_file_text(owner, name, kit)
            if text is None:
                continue
            found_any = True
            if marker_re.search(text):
                with_markers.append(f"{full}:{kit}")
        if not found_any:
            absent.append(full)
    if with_markers:
        checks.append(
            Check(
                "adopter-kit-markers",
                "FAIL",
                f"adopter kit still has unfilled {{{{ }}}} markers in: "
                f"{', '.join(with_markers)}. Re-run AGENT_SETUP Step 6 to "
                "materialize the kit from your config.",
            )
        )
    elif absent and len(absent) == len(supervised):
        checks.append(
            Check(
                "adopter-kit-markers",
                "WARN",
                f"no AGENTS.md/CLAUDE.md kit found in any supervised repo "
                f"({', '.join(absent)}). Install it (AGENT_SETUP Step 6) so agents "
                "load the merge-gate contract.",
            )
        )
    else:
        detail = "adopter kit present and fully materialized (no {{ }} markers)"
        if absent:
            detail += f" (no kit in: {', '.join(absent)})"
        checks.append(Check("adopter-kit-markers", "PASS", detail + "."))


def _check_decision_labels(probe, inbox_owner, inbox_repo, checks):
    client = probe.client_for(inbox_owner)
    if client is None:
        checks.append(Check("decision-labels", "SKIP", f"no App client for '{inbox_owner}'."))
        return
    names = sorted(
        lbl.get("name", "")
        for lbl in client.list_labels(inbox_owner, inbox_repo)
        if str(lbl.get("name", "")).startswith("decision:")
    )
    if names:
        checks.append(
            Check(
                "decision-labels",
                "INFO",
                f"decision:* labels present ({len(names)}): {', '.join(names)}. "
                "Informational — these are created by the bot at runtime, not at "
                "setup, so their absence is never a failure.",
            )
        )
    else:
        checks.append(
            Check(
                "decision-labels",
                "INFO",
                "no decision:* labels yet — normal before the first Quadrant-D "
                "event; the bot creates them on demand.",
            )
        )
