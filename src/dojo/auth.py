"""Access control for the family portal, REST API, remote MCP endpoint and inbound webhooks (DAN-166).

Rollout model: everything here is inert until ``REQUIRE_AUTH=true`` is set, so merging and
auto-deploying never locks out existing MCP clients. Once enabled:

* ``/mcp`` and the ``/api/*`` data endpoints need a ``DOJO_ACCESS_TOKEN``, sent as
  ``Authorization: Bearer <token>`` or as the portal's ``__session`` cookie (set by ``/login``).
  Never in the URL: query strings land in request logs (DAN-414).
* ``/`` redirects to ``/login`` without a valid session.
* The Twilio webhook must carry a valid ``X-Twilio-Signature``.

The cookie is named ``__session`` because Firebase Hosting strips every other cookie before
forwarding requests to Cloud Run.
"""

import base64
import hashlib
import hmac
import logging
import os
from typing import Iterable, List, Mapping, Optional
from urllib.parse import quote

from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from dojo.config import Settings, settings as default_settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "__session"

# Paths that never need the access token (they have their own auth or expose nothing private).
PUBLIC_PATHS = {"/login", "/logout", "/api/status", "/api/sms/webhook", "/favicon.ico"}
PUBLIC_PREFIXES = ("/api/cron/",)


def is_cloud_run() -> bool:
    return bool(os.getenv("K_SERVICE"))


def configured_tokens(s: Optional[Settings] = None) -> List[str]:
    s = s or default_settings
    return [t.strip() for t in (s.dojo_access_token or "").split(",") if t.strip()]


def token_is_valid(candidate: Optional[str], s: Optional[Settings] = None) -> bool:
    """Constant-time check of a presented token against every configured token."""
    if not candidate:
        return False
    ok = False
    for token in configured_tokens(s):
        ok |= hmac.compare_digest(candidate.encode(), token.encode())
    return ok


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _cookie(cookie_header: Optional[str], name: str) -> Optional[str]:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def requires_token(path: str) -> bool:
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return False
    return path == "/mcp" or path.startswith("/mcp/") or path.startswith("/api/") or path == "/"


def safe_next(target: Optional[str]) -> str:
    """Only allow same-site relative redirects after login."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


class AccessGuardMiddleware:
    """Pure ASGI middleware (keeps MCP's streaming responses intact) that enforces the access token."""

    def __init__(self, app: ASGIApp, settings: Optional[Settings] = None):
        self.app = app
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings or default_settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.settings.require_auth:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not requires_token(path):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        # Header or cookie only: a token in the URL would be stored in Cloud Run request logs (DAN-414).
        candidate = _bearer(headers.get("authorization")) or _cookie(headers.get("cookie"), SESSION_COOKIE)

        if token_is_valid(candidate, self.settings):
            await self.app(scope, receive, send)
            return

        if not configured_tokens(self.settings):
            logger.error("REQUIRE_AUTH is on but DOJO_ACCESS_TOKEN is empty; refusing all private requests.")

        if path == "/" and scope.get("method") == "GET":
            qs = scope.get("query_string", b"").decode("latin-1")
            target = "/" + (f"?{qs}" if qs else "")
            response = RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
        else:
            response = JSONResponse(
                {"detail": "Unauthorized: DojoZen access token required."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="dojo-zen"'},
            )
        await response(scope, receive, send)


# --- Twilio webhook signature (https://www.twilio.com/docs/usage/security#validating-requests) ---

def twilio_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def twilio_request_is_valid(
    auth_token: str,
    signature: Optional[str],
    candidate_urls: Iterable[str],
    params: Mapping[str, str],
) -> bool:
    """True if the signature matches any URL Twilio may have been configured to call."""
    if not auth_token or not signature:
        return False
    return any(
        hmac.compare_digest(twilio_signature(auth_token, url, params), signature)
        for url in dict.fromkeys(candidate_urls)
    )


# --- Cron secret ---

def cron_token_is_valid(token: Optional[str], s: Optional[Settings] = None) -> bool:
    """Fail closed: an unset CRON_SECRET rejects every cron call (set one locally to test crons)."""
    s = s or default_settings
    expected = s.cron_secret or ""
    if not expected:
        logger.error("CRON_SECRET is not set%s; rejecting cron call.", " on Cloud Run" if is_cloud_run() else "")
        return False
    return bool(token) and hmac.compare_digest(token.encode(), expected.encode())
