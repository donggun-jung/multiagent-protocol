"""Minimal GitHub REST API wrapper.

Just enough surface to support the bot's needs:

- list_open_prs(owner, repo)
- pr(owner, repo, number)
- pr_commits(owner, repo, number)  — paginated
- pr_files(owner, repo, number)    — paginated
- check_runs(owner, repo, sha)     — paginated
- main_head_sha(owner, repo)
- merge_pr(owner, repo, number, head_sha, method)  — with sha precondition (TOCTOU defeat)
- add_label / remove_label
- post_comment
- list_issues / open_issue / close_issue
- file_exists_at_sha(owner, repo, path, sha)
- list_commits_on_main(owner, repo, since_sha)

All calls go through ``_request`` which handles 5xx retry, pagination, and
the installation token refresh.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

import requests

from multiagent_protocol.auth import GITHUB_API, AppAuth

logger = logging.getLogger(__name__)

# Retry policy for 5xx and a few transient 4xx.
RETRY_STATUSES = {500, 502, 503, 504, 408, 429}
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_SECONDS = 1.0

# Secondary-rate-limit / abuse-detection back-off. A 403 (or 429) that carries
# ``Retry-After`` or a zero ``X-RateLimit-Remaining`` is GitHub asking us to
# slow down, NOT an auth failure — replaying it immediately (or letting it
# bubble up and crash the whole repo scan, which then re-runs every 5 min) only
# digs the hole deeper. We honour the hint with a bounded sleep and one retry.
SECONDARY_RATE_LIMIT_STATUSES = {403, 429}
# Cap how long we will block a single tick on a back-off hint (the tick itself
# has a 5-min budget; sleeping the full Retry-After could blow it).
MAX_RATE_LIMIT_SLEEP_SECONDS = 60.0


class SecondaryRateLimitError(RuntimeError):
    """Raised when GitHub's secondary rate limit is hit and back-off is exhausted.

    Distinct from a plain 403 so callers (the per-repo scan loop) can catch it,
    log it, and move on to the next repo instead of letting one throttled repo
    abort the whole tick and replay forever."""


# How many commit pages (×100) ``list_commits_on_main`` will walk looking for the
# since-SHA before giving up. A watermark that is not found within this window
# almost always means ``main`` history was rewritten out from under the bot
# (force-push / branch reset) — replaying the *entire* history then is both
# wrong (it re-floods L2/L5) and fatal to the 5-min tick budget. The caller
# treats ``SINCE_NOT_FOUND`` as "watermark lost" and re-bootstraps to HEAD.
LIST_COMMITS_MAX_PAGES = 10


class _SinceNotFound:
    """Sentinel returned by ``list_commits_on_main`` when the since-SHA is not
    reachable within ``LIST_COMMITS_MAX_PAGES`` (history rewritten off main)."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "SINCE_NOT_FOUND"


SINCE_NOT_FOUND = _SinceNotFound()


class GitHubAPI:
    """A simple REST client scoped to one installation."""

    def __init__(
        self,
        auth: AppAuth,
        installation_id: int,
        session: requests.Session | None = None,
    ) -> None:
        self.auth = auth
        self.installation_id = installation_id
        self._session = session or requests.Session()
        # Last observed ``X-RateLimit-Remaining`` (None until the first call
        # that returns the header). The orchestrator logs this each tick and may
        # end the tick gracefully when it drops below a reserve threshold.
        self.rate_limit_remaining: int | None = None

    # -- request internals --

    def _note_rate_limit(self, resp: requests.Response) -> None:
        raw = resp.headers.get("X-RateLimit-Remaining")
        if raw is None:
            return
        try:
            self.rate_limit_remaining = int(raw)
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _is_secondary_rate_limit(resp: requests.Response) -> bool:
        """True iff this response is a secondary-rate-limit / abuse signal.

        A 429 always is. A 403 is only a rate-limit (vs a genuine permission
        error) when it carries ``Retry-After`` or reports zero primary quota
        remaining — a bare 403 (missing scope) must still surface as an error."""
        if resp.status_code == 429:
            return True
        if resp.status_code != 403:
            return False
        if resp.headers.get("Retry-After") is not None:
            return True
        remaining = resp.headers.get("X-RateLimit-Remaining")
        return remaining is not None and remaining == "0"

    @classmethod
    def _rate_limit_sleep_seconds(cls, resp: requests.Response, attempt: int) -> float:
        """How long to back off for a throttled response (bounded)."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return min(float(retry_after), MAX_RATE_LIMIT_SLEEP_SECONDS)
            except (TypeError, ValueError):
                pass
        # No explicit hint → exponential back-off, capped.
        return min(RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt), MAX_RATE_LIMIT_SLEEP_SECONDS)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any | None = None,
    ) -> requests.Response:
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            token = self.auth.installation_token(self.installation_id)
            url = f"{GITHUB_API}{path}"
            try:
                resp = self._session.request(
                    method,
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    params=params,
                    json=json,
                    timeout=30,
                )
            except requests.RequestException as e:
                last_err = e
                self._backoff(attempt)
                continue

            self._note_rate_limit(resp)

            # Secondary / abuse rate limit: honour Retry-After (bounded), retry
            # once, then raise a typed error the scan loop can catch per-repo —
            # rather than crash the whole tick and replay forever (P1 fix #5c).
            if self._is_secondary_rate_limit(resp):
                if attempt < MAX_RETRIES:
                    sleep = self._rate_limit_sleep_seconds(resp, attempt)
                    logger.warning(
                        "github_api: %s %s -> %s secondary rate limit, backing "
                        "off %.1fs (%d/%d)",
                        method, path, resp.status_code, sleep, attempt + 1, MAX_RETRIES,
                    )
                    time.sleep(sleep)
                    continue
                raise SecondaryRateLimitError(
                    f"github_api: {method} {path} -> {resp.status_code} "
                    f"secondary rate limit, back-off exhausted"
                )

            if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                logger.warning(
                    "github_api: %s %s -> %s, retrying (%d/%d)",
                    method, path, resp.status_code, attempt + 1, MAX_RETRIES,
                )
                self._backoff(attempt)
                continue
            return resp

        if last_err is not None:
            raise last_err
        raise RuntimeError("github_api: exhausted retries with no response")

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt))

    def _paginate(self, path: str, params: dict | None = None) -> Iterable[Any]:
        page = 1
        params = dict(params or {})
        while True:
            params["per_page"] = 100
            params["page"] = page
            r = self._request("GET", path, params=params)
            r.raise_for_status()
            data = r.json()
            if not data:
                return
            yield from data
            if len(data) < 100:
                return
            page += 1

    # -- PR endpoints --

    def list_open_prs(self, owner: str, repo: str) -> list[dict]:
        return list(self._paginate(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "open", "sort": "created", "direction": "asc"},
        ))

    def pr(self, owner: str, repo: str, number: int) -> dict:
        r = self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        r.raise_for_status()
        return r.json()

    def pr_commits(self, owner: str, repo: str, number: int) -> list[dict]:
        return list(self._paginate(
            f"/repos/{owner}/{repo}/pulls/{number}/commits"
        ))

    def pr_files(self, owner: str, repo: str, number: int) -> list[dict]:
        return list(self._paginate(
            f"/repos/{owner}/{repo}/pulls/{number}/files"
        ))

    def check_runs(self, owner: str, repo: str, sha: str) -> list[dict]:
        """Return the check-runs on a commit.

        The check-runs endpoint does NOT return a bare list — it wraps results
        in a ``{"total_count": N, "check_runs": [...]}`` envelope. The generic
        ``_paginate`` (which ``yield from``s the body) would yield the dict's
        *keys* (``"total_count"``, ``"check_runs"``) instead of check-run
        objects, so this endpoint paginates itself and extracts the array.
        """
        runs: list[dict] = []
        page = 1
        while True:
            r = self._request(
                "GET", f"/repos/{owner}/{repo}/commits/{sha}/check-runs",
                params={"filter": "all", "per_page": 100, "page": page},
            )
            r.raise_for_status()
            data = r.json()
            batch = data.get("check_runs", []) if isinstance(data, dict) else []
            runs.extend(batch)
            total = data.get("total_count") if isinstance(data, dict) else None
            if len(batch) < 100 or (total is not None and len(runs) >= total):
                break
            page += 1
        return runs

    def label_events(self, owner: str, repo: str, number: int) -> list[dict]:
        """Return ``labeled`` events from the PR/issue timeline.

        Used by C1 to verify ``ready-to-merge`` was applied by an allowlisted
        actor — not merely present. Each entry is
        ``{"label", "actor", "created_at"}``. The GitHub timeline API is GA;
        no preview media type is required.
        """
        events: list[dict] = []
        for e in self._paginate(f"/repos/{owner}/{repo}/issues/{number}/timeline"):
            if e.get("event") != "labeled":
                continue
            events.append({
                "label": (e.get("label") or {}).get("name"),
                "actor": (e.get("actor") or {}).get("login"),
                "created_at": e.get("created_at", ""),
            })
        return events

    def main_head_sha(self, owner: str, repo: str) -> str:
        r = self._request("GET", f"/repos/{owner}/{repo}/branches/main")
        r.raise_for_status()
        return r.json()["commit"]["sha"]

    def list_commits_on_main(
        self,
        owner: str,
        repo: str,
        since_sha: str | None = None,
        *,
        max_pages: int = LIST_COMMITS_MAX_PAGES,
    ) -> list[dict] | _SinceNotFound:
        """Newest-first commits on ``main`` since ``since_sha`` (exclusive).

        GitHub has no "since SHA" query, so we page newest-first and stop at
        ``since_sha``. The page walk is BOUNDED (``max_pages`` × 100): if
        ``since_sha`` is not reached within that window, ``main`` was almost
        certainly rewritten out from under the watermark — we return
        :data:`SINCE_NOT_FOUND` so the caller re-bootstraps to HEAD rather than
        replaying the whole history (which both re-floods and blows the tick
        budget). With ``since_sha=None`` the walk is still bounded but always
        returns the (capped) list — a cold scan has no anchor to miss.
        """
        results: list[dict] = []
        pages = 0
        for c in self._paginate(f"/repos/{owner}/{repo}/commits", params={"sha": "main"}):
            if since_sha is not None and c["sha"] == since_sha:
                return results
            results.append(c)
            # ``_paginate`` yields 100 per page; count page boundaries to bound
            # the since-search without depending on the generator's internals.
            if len(results) % 100 == 0:
                pages += 1
                if pages >= max_pages:
                    if since_sha is not None:
                        # Walked the cap and never hit the anchor → history lost.
                        return SINCE_NOT_FOUND
                    return results
        return results

    def merge_pr(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        head_sha: str,
        method: str = "squash",
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> dict:
        """Merge a PR with a head-SHA precondition (defeats TOCTOU race)."""
        body: dict = {
            "sha": head_sha,
            "merge_method": method,
        }
        if commit_title:
            body["commit_title"] = commit_title
        if commit_message:
            body["commit_message"] = commit_message
        r = self._request(
            "PUT", f"/repos/{owner}/{repo}/pulls/{number}/merge", json=body
        )
        r.raise_for_status()
        return r.json()

    # -- Labels + comments --

    def add_label(self, owner: str, repo: str, number: int, label: str) -> None:
        r = self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/labels",
            json={"labels": [label]},
        )
        r.raise_for_status()

    def remove_label(self, owner: str, repo: str, number: int, label: str) -> None:
        # GitHub returns 404 if label is not on the issue — treat as idempotent.
        r = self._request(
            "DELETE", f"/repos/{owner}/{repo}/issues/{number}/labels/{label}"
        )
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    def post_comment(self, owner: str, repo: str, number: int, body: str) -> None:
        r = self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json={"body": body},
        )
        r.raise_for_status()

    # -- Issues --

    def list_issues(
        self, owner: str, repo: str, *, labels: str | None = None, state: str = "open"
    ) -> list[dict]:
        params: dict = {"state": state}
        if labels is not None:
            params["labels"] = labels
        return list(self._paginate(f"/repos/{owner}/{repo}/issues", params=params))

    def open_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict:
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        r = self._request(
            "POST", f"/repos/{owner}/{repo}/issues", json=payload
        )
        r.raise_for_status()
        return r.json()

    def close_issue(self, owner: str, repo: str, number: int) -> None:
        r = self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{number}",
            json={"state": "closed"},
        )
        r.raise_for_status()

    def list_issue_reactions(self, owner: str, repo: str, number: int) -> list[dict]:
        return list(self._paginate(
            f"/repos/{owner}/{repo}/issues/{number}/reactions"
        ))

    def list_issue_comments(self, owner: str, repo: str, number: int) -> list[dict]:
        return list(self._paginate(
            f"/repos/{owner}/{repo}/issues/{number}/comments"
        ))

    # -- File existence --

    def file_exists_at_sha(self, owner: str, repo: str, path: str, sha: str) -> bool:
        r = self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": sha},
        )
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return False  # unreachable

    def get_file_sha256(self, owner: str, repo: str, path: str, ref: str = "main") -> str | None:
        """Return SHA-256 of a file's content at ``ref``, or None if absent."""
        r = self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        import base64
        import hashlib

        data = r.json()
        if data.get("encoding") != "base64":
            return None
        content = base64.b64decode(data["content"])
        return hashlib.sha256(content).hexdigest()

    def get_contents(self, owner: str, repo: str, path: str, ref: str = "main"):
        """Return GitHub's ``contents`` JSON for a file or directory.

        A file yields a dict; a directory yields a list. Returns ``None`` on
        404 so callers can treat absence as a normal case.
        """
        r = self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_file_text(
        self, owner: str, repo: str, path: str, ref: str = "main"
    ) -> str | None:
        """Return the decoded UTF-8 text of a file at ``ref``, or None if absent."""
        import base64

        data = self.get_contents(owner, repo, path, ref)
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return None
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    def list_dir(self, owner: str, repo: str, path: str, ref: str = "main") -> list[dict]:
        """Return directory entries at ``path`` (empty list if absent / not a dir)."""
        data = self.get_contents(owner, repo, path, ref)
        return data if isinstance(data, list) else []

    # -- Branch update (L3 auto-rebase) --

    def update_branch(self, owner: str, repo: str, number: int) -> bool:
        """Update the PR branch with its base (GitHub's "Update branch" button).

        Used by the L3 race-guard when ``main`` advanced past the PR's base:
        rather than merge against a stale base, the bot rebases the PR branch
        and lets the next tick re-evaluate. Returns True if GitHub accepted the
        update (HTTP 202); False if there was nothing to do or a conflict
        (HTTP 422) — in either case the merge is not attempted this tick.
        """
        r = self._request(
            "PUT", f"/repos/{owner}/{repo}/pulls/{number}/update-branch"
        )
        return r.status_code == 202

    # -- Git refs + durable file write (bot-state branch persistence) --

    def get_ref_sha(self, owner: str, repo: str, ref: str) -> str | None:
        """Return the commit SHA a branch ref points at, or None if it is absent.

        ``ref`` is the short branch name (e.g. ``bot-state``)."""
        r = self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{ref}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        return (data.get("object") or {}).get("sha")

    def create_ref(self, owner: str, repo: str, ref: str, sha: str) -> None:
        """Create a branch ``ref`` pointing at ``sha`` (e.g. the bot-state branch).

        ``ref`` is the short branch name; GitHub wants the fully-qualified
        ``refs/heads/<name>`` in the request body."""
        r = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{ref}", "sha": sha},
        )
        r.raise_for_status()

    def get_file_on_ref(
        self, owner: str, repo: str, path: str, ref: str
    ) -> tuple[str, str] | None:
        """Return ``(decoded_text, blob_sha)`` for a file on ``ref``, or None.

        The blob SHA is required as the ``sha`` precondition when updating the
        file via :meth:`put_file_on_ref`."""
        import base64

        data = self.get_contents(owner, repo, path, ref)
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return None
        text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return text, data.get("sha", "")

    def put_file_on_ref(
        self,
        owner: str,
        repo: str,
        path: str,
        *,
        ref: str,
        content: str,
        message: str,
        blob_sha: str | None = None,
    ) -> str:
        """Create or update ``path`` on branch ``ref``; return the new blob SHA.

        ``blob_sha`` is the current file blob SHA (from :meth:`get_file_on_ref`)
        when updating an existing file, omitted when creating it. The commit
        lands on ``ref`` only — the caller persists watermarks to a DEDICATED
        ``bot-state`` branch so the engine's own ``main`` scans never see it."""
        import base64

        body: dict = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": ref,
        }
        if blob_sha:
            body["sha"] = blob_sha
        r = self._request(
            "PUT", f"/repos/{owner}/{repo}/contents/{path}", json=body
        )
        r.raise_for_status()
        data = r.json()
        return ((data.get("content") or {}).get("sha")) or ""

    def commit_merged_by(self, owner: str, repo: str, sha: str) -> str | None:
        """Resolve the true merge actor (``merged_by``) for a commit on ``main``.

        A squash/rebase merge lands on ``main`` with committer ``web-flow`` (or
        the App), masking *who* clicked merge. The
        ``GET /commits/{sha}/pulls`` endpoint maps the commit back to its PR;
        ``merged_by.login`` is the actor that performed the merge. Returns None
        if the commit is not associated with a merged PR (e.g. a direct push).
        """
        r = self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits/{sha}/pulls",
            params={"per_page": 100},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        prs = r.json()
        if not isinstance(prs, list):
            return None
        for pr in prs:
            if pr.get("merge_commit_sha") == sha:
                merged_by = (pr.get("merged_by") or {}).get("login")
                if merged_by:
                    return merged_by
        # Fall back to the first associated PR's merger (older squash commits do
        # not always echo merge_commit_sha back through this endpoint).
        for pr in prs:
            merged_by = (pr.get("merged_by") or {}).get("login")
            if merged_by:
                return merged_by
        return None
