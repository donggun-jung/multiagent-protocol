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

    # -- request internals --

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
        self, owner: str, repo: str, since_sha: str | None = None
    ) -> list[dict]:
        # GitHub does not have a "since SHA" query directly; we fetch a page
        # and stop at since_sha if found.
        results: list[dict] = []
        for c in self._paginate(f"/repos/{owner}/{repo}/commits", params={"sha": "main"}):
            if since_sha is not None and c["sha"] == since_sha:
                break
            results.append(c)
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
