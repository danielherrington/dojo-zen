"""Access control for portal, /api/*, /mcp, Twilio webhook and cron (DAN-166)."""

import pytest
from fastapi.testclient import TestClient

from dojo.auth import safe_next, twilio_signature
from dojo.config import settings
from dojo.server import app

TOKEN = "family-test-token-123"
MCP_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "pytest", "version": "1"}},
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "dojo_access_token", f"other-token,{TOKEN}")


@pytest.fixture
def client():
    # No lifespan: the MCP session manager may only start once per process and the
    # end-to-end MCP client test in test_mcp_server.py owns that run.
    return TestClient(app)


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"inner-app"})


@pytest.fixture
def guarded():
    """The guard wrapped around a stand-in app, to test /mcp routing without starting MCP."""
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from dojo.auth import AccessGuardMiddleware
    inner = Starlette(routes=[Mount("/", app=_ok_app)])
    return TestClient(AccessGuardMiddleware(inner))


@pytest.mark.parametrize("method,path", [
    ("get", "/api/feed"), ("get", "/api/briefing"), ("post", "/api/ask"),
    ("get", "/api/feed/abc"), ("post", "/api/sync"), ("post", "/api/feed/abc/analyze"),
])
def test_private_api_requires_token(client, locked, method, path):
    kwargs = {"json": {"query": "homework"}} if path == "/api/ask" else {}
    res = getattr(client, method)(path, **kwargs)
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith("Bearer")


def test_bearer_token_grants_api_access(client, locked):
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/feed?limit=5", headers=hdr).status_code == 200
    assert client.get("/api/briefing", headers=hdr).status_code == 200
    assert client.post("/api/ask", json={"query": "homework"}, headers=hdr).status_code == 200


def test_wrong_token_rejected(client, locked):
    assert client.get("/api/feed", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_status_stays_public(client, locked):
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json()["status"] == "online"


def test_index_redirects_to_login_and_cookie_login_works(client, locked):
    res = client.get("/?item=42", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/login?next=")

    bad = client.post("/login", data={"token": "nope", "next": "/"}, follow_redirects=False)
    assert bad.status_code == 401

    ok = client.post("/login", data={"token": TOKEN, "next": "/?item=42"}, follow_redirects=False)
    assert ok.status_code == 303
    assert ok.headers["location"] == "/?item=42"
    set_cookie = ok.headers["set-cookie"]
    assert set_cookie.startswith("__session=") and "HttpOnly" in set_cookie

    cookie = {"Cookie": f"__session={TOKEN}"}
    assert client.get("/", headers=cookie).status_code == 200
    assert client.get("/api/feed", headers=cookie).status_code == 200


def test_login_rejects_open_redirect():
    assert safe_next("https://evil.example") == "/"
    assert safe_next("//evil.example") == "/"
    assert safe_next("/\\evil.example") == "/"
    assert safe_next("/?item=1") == "/?item=1"


def test_mcp_requires_token(client, locked):
    res = client.post("/mcp", json=MCP_INIT, headers=MCP_HEADERS)
    assert res.status_code == 401
    assert client.post("/mcp/", json=MCP_INIT, headers=MCP_HEADERS).status_code == 401


def test_mcp_accepts_bearer_and_query_token(guarded, locked):
    assert guarded.post("/mcp", json=MCP_INIT).status_code == 401
    res = guarded.post("/mcp", json=MCP_INIT, headers={"Authorization": f"Bearer {TOKEN}"})
    assert res.status_code == 200 and res.text == "inner-app"
    assert guarded.post(f"/mcp/?access_token={TOKEN}", json=MCP_INIT).status_code == 200
    assert guarded.post("/mcp?access_token=wrong", json=MCP_INIT).status_code == 401


def test_query_token_only_allowed_for_mcp(client, locked):
    assert client.get(f"/api/feed?access_token={TOKEN}").status_code == 401


def test_empty_token_config_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "dojo_access_token", "")
    assert client.get("/api/feed", headers={"Authorization": "Bearer "}).status_code == 401
    assert client.post("/login", data={"token": ""}).status_code in (401, 422)


def test_auth_off_by_default_keeps_everything_open(client, monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    assert client.get("/api/feed").status_code == 200
    assert client.get("/").status_code == 200


def test_guard_passthrough_when_auth_off(guarded, monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    assert guarded.post("/mcp", json=MCP_INIT).status_code == 200


# --- Twilio signature ---

SMS_FORM = {"From": "+13015550100", "Body": "homework?", "To": "+13015559999", "MessageSid": "SM1"}


@pytest.fixture
def twilio(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", "twilio-secret")
    monkeypatch.setattr(settings, "family_phone_numbers", "+13015550100")
    monkeypatch.setattr(settings, "app_base_url", "https://dojo.example.test")


def test_twilio_signature_known_vector():
    # Example from Twilio's request-validation docs.
    params = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234",
              "From": "+12349013030", "To": "+18005551212"}
    sig = twilio_signature("12345", "https://mycompany.com/myapp.php?foo=1&bar=2", params)
    assert sig == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="


def test_sms_valid_signature_accepted(client, locked, twilio):
    sig = twilio_signature("twilio-secret", "https://dojo.example.test/api/sms/webhook", SMS_FORM)
    res = client.post("/api/sms/webhook", data=SMS_FORM, headers={"X-Twilio-Signature": sig})
    assert res.status_code == 200
    assert "<Response>" in res.text


def test_sms_spoofed_whitelisted_number_rejected(client, locked, twilio):
    res = client.post("/api/sms/webhook", data=SMS_FORM)
    assert res.status_code == 403
    res = client.post("/api/sms/webhook", data=SMS_FORM, headers={"X-Twilio-Signature": "forged="})
    assert res.status_code == 403


def test_sms_unsigned_allowed_while_auth_off(client, twilio, monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    assert client.post("/api/sms/webhook", data=SMS_FORM).status_code == 200


# --- Cron secret ---

@pytest.mark.parametrize("cloud_run", [True, False])
def test_cron_fails_closed_without_secret(client, monkeypatch, cloud_run):
    if cloud_run:
        monkeypatch.setenv("K_SERVICE", "dojo-zen")
    else:
        monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(settings, "cron_secret", "")
    res = client.post("/api/cron/check-alerts?secret=dojo-zen-cron-token")
    assert res.status_code == 401
    res = client.post("/api/cron/daily-digest", headers={"Authorization": "Bearer anything"})
    assert res.status_code == 401


def test_cron_old_default_secret_no_longer_works(client, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", "real-secret")
    assert client.post("/api/cron/check-alerts?secret=dojo-zen-cron-token").status_code == 401
