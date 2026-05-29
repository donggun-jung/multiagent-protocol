"""GitHub App authentication: JWT + installation token.

The bot authenticates as a GitHub App. The flow is:

1. Build an App JWT signed with the App's RSA private key (1-min expiry).
2. Exchange the JWT for an **installation token** (1-hour expiry) for a
   specific App installation (one per (App, account, repo-selection)).
3. Use the installation token as a Bearer for REST API calls.

Tokens are cached per-installation per-process; they refresh on demand.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import jwt as pyjwt
import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# JWT expires_in field — GitHub allows up to 10 minutes; we use 60s for safety.
JWT_TTL_SECONDS = 60

# Refresh installation token when it has this much (or less) life remaining.
INSTALLATION_TOKEN_REFRESH_SECONDS = 5 * 60


@dataclass(frozen=True)
class AppCredentials:
    """The App ID + PEM private key. Read from env vars at startup."""

    app_id: str
    private_key_pem: str


class AppAuth:
    """Authenticates as a GitHub App and caches installation tokens."""

    def __init__(self, credentials: AppCredentials, session: requests.Session | None = None) -> None:
        self.credentials = credentials
        self._session = session or requests.Session()
        # installation_id -> (token, expires_at_epoch)
        self._installation_tokens: dict[int, tuple[str, float]] = {}
        self._app_slug_value: str | None = None
        self._app_slug_fetched = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AppAuth:
        """Construct from ``MERGE_GATE_APP_ID`` + ``MERGE_GATE_PRIVATE_KEY``."""
        import os

        env = env if env is not None else dict(os.environ)
        app_id = env.get("MERGE_GATE_APP_ID")
        pem = env.get("MERGE_GATE_PRIVATE_KEY")
        if not app_id or not pem:
            raise RuntimeError(
                "MERGE_GATE_APP_ID and MERGE_GATE_PRIVATE_KEY must be set"
            )
        # GitHub UI sometimes writes the PEM with literal "\n" escapes; un-escape.
        pem = pem.replace("\\n", "\n")
        return cls(AppCredentials(app_id=app_id, private_key_pem=pem))

    def build_app_jwt(self, now: float | None = None) -> str:
        """Build an App JWT good for ~60 seconds."""
        now_epoch = int(now if now is not None else time.time())
        payload = {
            "iat": now_epoch - 10,  # tolerate small clock skew
            "exp": now_epoch + JWT_TTL_SECONDS,
            "iss": self.credentials.app_id,
        }
        return pyjwt.encode(
            payload, self.credentials.private_key_pem, algorithm="RS256"
        )

    def installations(self) -> list[dict]:
        """List all installations of this App."""
        token = self.build_app_jwt()
        r = self._session.get(
            f"{GITHUB_API}/app/installations",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def app_slug(self) -> str | None:
        """Return this App's slug (for building the bot user login).

        The merge gate verifies ``decision:approved-*`` labels against the
        bot's OWN identity (``<slug>[bot]``) rather than operator-typed
        ``config/env.yml`` ``bot_app_slug`` — so a mistyped slug cannot
        silently break the approve→merge flow. Cached for the process; returns
        None if the lookup fails (the caller then falls back to config).
        """
        if self._app_slug_fetched:
            return self._app_slug_value
        self._app_slug_fetched = True
        try:
            token = self.build_app_jwt()
            r = self._session.get(
                f"{GITHUB_API}/app",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30,
            )
            r.raise_for_status()
            self._app_slug_value = r.json().get("slug")
        except Exception as e:
            logger.warning("could not resolve App slug (using config fallback): %s", e)
            self._app_slug_value = None
        return self._app_slug_value

    def installation_token(self, installation_id: int, now: float | None = None) -> str:
        """Return a valid installation access token, refreshing if needed."""
        now_epoch = now if now is not None else time.time()
        cached = self._installation_tokens.get(installation_id)
        if cached is not None:
            token, expires_at = cached
            if expires_at - now_epoch > INSTALLATION_TOKEN_REFRESH_SECONDS:
                return token

        jwt_token = self.build_app_jwt(now=now_epoch)
        r = self._session.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        token = data["token"]
        # expires_at is ISO 8601; convert to epoch.
        from datetime import datetime, timezone

        expires_dt = datetime.strptime(
            data["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        expires_epoch = expires_dt.timestamp()
        self._installation_tokens[installation_id] = (token, expires_epoch)
        logger.info(
            "issued installation token for installation_id=%s, expires_at=%s",
            installation_id,
            data["expires_at"],
        )
        return token
