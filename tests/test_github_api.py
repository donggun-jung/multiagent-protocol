"""Unit tests for the GitHub REST client + App auth — previously 0 coverage.

The most important case here is ``check_runs``: the live check-runs endpoint
returns a ``{"total_count", "check_runs": [...]}`` envelope, not a bare list,
and an earlier version returned the dict's keys instead of the check-run
objects (the FakeAPI in conftest serves lists directly, so it hid the bug).
"""

from __future__ import annotations

from multiagent_protocol.auth import AppAuth, AppCredentials
from multiagent_protocol.github_api import GitHubAPI


class _Resp:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeRequestAPI(GitHubAPI):
    """GitHubAPI whose ``_request`` returns canned payloads in order."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls: list[tuple] = []

    def _request(self, method, path, *, params=None, json=None):
        self.calls.append((method, path, dict(params or {})))
        return _Resp(self._payloads.pop(0))


# -- check_runs: the P0 envelope bug --

def test_check_runs_unwraps_envelope():
    api = _FakeRequestAPI([
        {"total_count": 2, "check_runs": [{"name": "lint"}, {"name": "test"}]},
    ])
    runs = api.check_runs("o", "r", "sha")
    assert [c["name"] for c in runs] == ["lint", "test"]


def test_check_runs_empty():
    api = _FakeRequestAPI([{"total_count": 0, "check_runs": []}])
    assert api.check_runs("o", "r", "sha") == []


def test_check_runs_paginates_over_100():
    page1 = {"total_count": 150, "check_runs": [{"name": f"c{i}"} for i in range(100)]}
    page2 = {"total_count": 150, "check_runs": [{"name": f"c{i}"} for i in range(100, 150)]}
    api = _FakeRequestAPI([page1, page2])
    runs = api.check_runs("o", "r", "sha")
    assert len(runs) == 150
    assert runs[-1]["name"] == "c149"


# -- label_events: list paginator + labeled/unlabeled filter --

def test_label_events_filters_to_labeled_and_unlabeled():
    # Both ``labeled`` and ``unlabeled`` are carried (each tagged with its
    # ``event`` kind); other timeline events (e.g. ``commented``) are dropped.
    api = _FakeRequestAPI([[
        {"event": "labeled", "label": {"name": "ready-to-merge"},
         "actor": {"login": "owner"}, "created_at": "2026-05-25T00:00:00Z"},
        {"event": "commented", "actor": {"login": "x"}},
        {"event": "unlabeled", "label": {"name": "ready-to-merge"},
         "actor": {"login": "owner"}, "created_at": "2026-05-25T00:02:00Z"},
        {"event": "labeled", "label": {"name": "documentation"},
         "actor": {"login": "bot[bot]"}, "created_at": "2026-05-25T00:01:00Z"},
    ]])
    evs = api.label_events("o", "r", 1)
    assert evs == [
        {"event": "labeled", "label": "ready-to-merge", "actor": "owner",
         "created_at": "2026-05-25T00:00:00Z"},
        {"event": "unlabeled", "label": "ready-to-merge", "actor": "owner",
         "created_at": "2026-05-25T00:02:00Z"},
        {"event": "labeled", "label": "documentation", "actor": "bot[bot]",
         "created_at": "2026-05-25T00:01:00Z"},
    ]


# -- AppAuth.app_slug: the self-derive bot identity (was 0-test) --

class _Sess:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def get(self, url, **kw):
        self.calls += 1
        return _Resp(self._payload)


def test_app_slug_resolves_and_caches():
    sess = _Sess({"slug": "my-merge-gate-bot"})
    auth = AppAuth(AppCredentials(app_id="1", private_key_pem="x"), session=sess)
    auth.build_app_jwt = lambda now=None: "fake-jwt"  # avoid signing a fake PEM
    assert auth.app_slug() == "my-merge-gate-bot"
    assert auth.app_slug() == "my-merge-gate-bot"   # cached
    assert sess.calls == 1                            # exactly one GET /app


def test_app_slug_failure_returns_none():
    class _BadSess:
        def get(self, *a, **k):
            raise RuntimeError("network down")

    auth = AppAuth(AppCredentials(app_id="1", private_key_pem="x"), session=_BadSess())
    auth.build_app_jwt = lambda now=None: "fake-jwt"
    assert auth.app_slug() is None      # fails safe → caller uses config fallback
