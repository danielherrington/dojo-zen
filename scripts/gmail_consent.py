#!/usr/bin/env python3
"""One-time Gmail consent for the Lehrman email sync (DAN-395). Read-only scope only.

Opens Google's consent page in the browser for ``gmail.readonly``, catches the redirect on a
localhost port, exchanges the code (PKCE) and stores three secrets in Secret Manager without
printing them: dojo-zen-gmail-client-id, dojo-zen-gmail-client-secret, dojo-zen-gmail-refresh-token.
It also grants the Cloud Run runtime account secretAccessor on each. Nothing else changes; wiring
the secrets into the service is part of the next deploy.

Needs an OAuth client of type "Desktop app" from the `dojo-zen-herrington` project (DAN-550), which
exists only for this sign-in: its consent screen is published "In production" so the refresh token
doesn't expire after 7 days. The secrets still live in `herrington-ai-site`, where the service runs.

Usage:
    uv run python scripts/gmail_consent.py path/to/client_secret.json
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import webbrowser

import httpx

GCLOUD = os.path.expanduser("~/google-cloud-sdk/bin/gcloud")
PROJECT = "herrington-ai-site"
RUNTIME_SA = "647998166555-compute@developer.gserviceaccount.com"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _store(secret_id: str, value: str) -> None:
    def gcloud(*args, data=None, check=True):
        return subprocess.run([GCLOUD, *args, "--project", PROJECT], input=data, text=True,
                              capture_output=True, check=check)

    if gcloud("secrets", "describe", secret_id, check=False).returncode != 0:
        gcloud("secrets", "create", secret_id, "--replication-policy", "automatic")
    gcloud("secrets", "versions", "add", secret_id, "--data-file=-", data=value)
    gcloud("secrets", "add-iam-policy-binding", secret_id, "--member", f"serviceAccount:{RUNTIME_SA}",
           "--role", "roles/secretmanager.secretAccessor")
    print(f"  stored {secret_id}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    conf = json.load(open(sys.argv[1]))
    conf = conf.get("installed") or conf.get("web") or conf
    client_id, client_secret = conf["client_id"], conf["client_secret"]

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                result.update(code=q.get("code", [""])[0], error=q.get("error", [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab.")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):  # keep the code out of the terminal
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("Opening Google consent (read-only Gmail). If no browser opens, visit:\n" + url)
    webbrowser.open(url)
    server.serve_forever()

    if not result.get("code"):
        print(f"Consent not completed: {result.get('error') or 'no code'}")
        return 1
    tokens = httpx.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "code": result["code"],
        "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code",
    }, timeout=30).json()
    if "refresh_token" not in tokens:
        print(f"No refresh token returned ({tokens.get('error', 'unknown error')}).")
        return 1
    if SCOPE not in tokens.get("scope", "").split():
        print("Granted scope is not gmail.readonly; refusing to store it.")
        return 1

    _store("dojo-zen-gmail-client-id", client_id)
    _store("dojo-zen-gmail-client-secret", client_secret)
    _store("dojo-zen-gmail-refresh-token", tokens["refresh_token"])
    print("Done. Next deploy maps them to GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
