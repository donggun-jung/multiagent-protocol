"""FEATURE B — L4 60-day burn-in auto-promotion (opt-in, default OFF).

Doctrine (``docs/concepts/four-quadrants.md`` § "L4 burn-in"): a new agent
vendor/model added to ``agent_registry.yml`` starts **advisory** — the L4
identity gate (``validator_agent_registry``, severity ``P2``) warns but does
not block — and is promoted to a hard block after a burn-in window (60 days) so
that "every new agent vendor breaks every PR" never happens.

v1.1 shipped the gate advisory-only; the operator promoted it manually with
``config/skills.yml`` ``severity_overrides: {validator_agent_registry: P0}``.
This module ships the **automatic** clock.

Behaviour, each tick, when ``env.yml`` ``l4_burn_in_days`` is a positive int:

* The clock's start (``advisory_started_at``) is written to a small JSON file
  on the bot-state BRANCH — ``bot-state/l4_burn_in.json`` — the FIRST tick the
  feature is on and the registry validator is at advisory severity.
* Once ``now >= advisory_started_at + l4_burn_in_days``, the effective severity
  of ``validator_agent_registry`` is promoted to ``P0`` (hard block).
* **The operator always wins.** If ``skills.severity_overrides`` pins
  ``validator_agent_registry`` (in EITHER direction — P0 or back to P2), the
  burn-in clock does nothing: explicit config is authoritative and the file is
  not even managed.

**Fail-SAFE (the opposite of the watermark store).** A missing or corrupt state
file is REWRITTEN with a fresh ``now`` start and the gate stays advisory this
tick. A watermark fails *closed* (never silently skip a scan); a burn-in clock
fails *safe* (never accidentally hard-block on unreadable state) — a corrupt
clock must not be the reason a PR is blocked.

**Log once.** The info line announcing that promotion is active is emitted only
on the tick the threshold is first crossed; a durable ``promoted_at`` field in
the same file keeps later ticks quiet (the bot is stateless across ticks).

Clock is injected (``now`` parameter) so tests control time; the logic never
calls ``datetime.now()`` inline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# The registry validator whose severity the burn-in clock promotes.
REGISTRY_VALIDATOR = "validator_agent_registry"

ADVISORY_SEVERITY = "P2"
PROMOTED_SEVERITY = "P0"

# The bot-state branch + file that hold the durable burn-in clock. The branch
# is created/ensured by BotStateStore.load() (watermarks) earlier in the tick,
# so this store only reads/writes the file on the already-present branch.
BOT_STATE_BRANCH = "bot-state"
L4_BURN_IN_PATH = "bot-state/l4_burn_in.json"

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_ISO_FMT)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _ISO_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class L4BurnInStore:
    """Reads/writes ``bot-state/l4_burn_in.json`` on the bot-state branch.

    FAIL-SAFE by design: any read problem (absent / unreadable / corrupt JSON /
    wrong shape) surfaces as ``{}`` (treat as "clock not started"), NOT an
    exception — an unreadable burn-in clock must never hard-block a PR. Writes
    are best-effort; a write failure is logged and swallowed (the clock simply
    re-starts next tick), never crashing the tick."""

    def __init__(
        self,
        api,
        owner: str,
        repo: str,
        *,
        branch: str = BOT_STATE_BRANCH,
        path: str = L4_BURN_IN_PATH,
    ) -> None:
        self.api = api
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.path = path
        self._blob_sha: str | None = None

    def read(self) -> dict[str, Any]:
        """Return the parsed state dict, or ``{}`` on any read problem."""
        try:
            found = self.api.get_file_on_ref(
                self.owner, self.repo, self.path, self.branch
            )
        except Exception as e:  # noqa: BLE001 - fail-safe
            logger.warning("L4 burn-in: state read failed (fail-safe empty): %s", e)
            return {}
        if not found:
            return {}
        text, blob_sha = found
        self._blob_sha = blob_sha
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "L4 burn-in: state file %s corrupt — rewriting with a fresh "
                "start (fail-safe advisory)", self.path,
            )
            return {}
        if not isinstance(data, dict):
            logger.warning("L4 burn-in: state file %s not an object — rewriting", self.path)
            return {}
        return data

    def write(self, state: dict[str, Any]) -> None:
        """Persist ``state`` (best-effort; a failure is logged, not raised)."""
        payload = json.dumps(state, indent=2, sort_keys=True)
        try:
            new_sha = self.api.put_file_on_ref(
                self.owner, self.repo, self.path,
                ref=self.branch, content=payload,
                message="chore(bot-state): update L4 burn-in clock",
                blob_sha=self._blob_sha,
            )
            self._blob_sha = new_sha or self._blob_sha
        except Exception as e:  # noqa: BLE001 - best-effort; clock re-starts next tick
            self._blob_sha = None
            logger.warning("L4 burn-in: state write failed (retry next tick): %s", e)


@dataclass(frozen=True)
class BurnInOutcome:
    """What the burn-in evaluation did this tick (for logging + metrics)."""

    active: bool          # promotion is currently in effect (severity == P0)
    just_promoted: bool   # the threshold was crossed THIS tick (log once)
    reason: str           # human-readable summary


def _find_registry_validator(runtime) -> Any | None:
    for v in getattr(runtime, "validators", []):
        if getattr(v, "name", None) == REGISTRY_VALIDATOR:
            return v
    return None


def apply_burn_in(
    runtime,
    config,
    store: L4BurnInStore,
    *,
    now: datetime,
) -> BurnInOutcome:
    """Apply the burn-in promotion to ``runtime`` in place. Never raises.

    Returns a :class:`BurnInOutcome`. The caller (``main.py``) logs the
    ``just_promoted`` info line and records the metric; the SEVERITY mutation on
    the ``validator_agent_registry`` instance is the operative side effect.
    """
    days = config.env.l4_burn_in_days
    if not days or days <= 0:
        return BurnInOutcome(False, False, "disabled (l4_burn_in_days=0)")

    validator = _find_registry_validator(runtime)
    if validator is None:
        # The registry validator is not in the runtime (disabled, or no
        # agent_registry). Nothing to promote.
        return BurnInOutcome(False, False, "validator_agent_registry not loaded")

    # The operator ALWAYS wins: an explicit severity_overrides entry (in either
    # direction) is authoritative — the burn-in clock does nothing and the file
    # is not managed. _apply_severity already set the pinned severity in
    # build_runtime_skills; leave it.
    if REGISTRY_VALIDATOR in getattr(runtime, "severity_overrides", {}):
        return BurnInOutcome(
            False, False,
            f"operator pinned severity_overrides[{REGISTRY_VALIDATOR}] — "
            f"burn-in clock inert (explicit config wins)",
        )

    state = store.read()
    started = _parse(state.get("advisory_started_at"))
    dirty = False
    if started is None:
        # First tick the feature is on (or a corrupt/missing file, fail-safe):
        # start the clock at now and stay advisory this tick.
        started = now
        state = {"advisory_started_at": _fmt(now)}
        dirty = True

    threshold = started + timedelta(days=days)
    promote = now >= threshold

    if not promote:
        if dirty:
            store.write(state)
        remaining = threshold - now
        return BurnInOutcome(
            False, False,
            f"advisory: {remaining.days}d until promotion "
            f"(started {_fmt(started)}, window {days}d)",
        )

    # Promotion is in effect → hard-block.
    validator.severity = PROMOTED_SEVERITY
    just_promoted = "promoted_at" not in state
    if just_promoted:
        state["promoted_at"] = _fmt(now)
        dirty = True
    if dirty:
        store.write(state)
    return BurnInOutcome(
        True, just_promoted,
        f"promoted validator_agent_registry to {PROMOTED_SEVERITY} "
        f"(burn-in of {days}d elapsed since {_fmt(started)})",
    )
