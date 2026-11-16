"""Email open/click analytics and usage-pattern counters (DAN-200)."""

import email
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from dojo import analytics
from dojo.analytics import UsageMiddleware, instrument_html, new_email_id, summarize, usage_channel
from dojo.config import Settings
from dojo.db import DojoDatabase
from dojo.digest import ActionItem, Briefing
from dojo.firestore_db import FirestoreDojoDatabase
from dojo.mailer import Mailer

from test_firestore_db import MockFirestoreClient

BASE = "https://dojo.example.test"
NOW = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _long_retention(monkeypatch):
    """These tests seed fixed 2026 dates; keep retention (DAN-205) from purging them as the real clock moves."""
    from dojo.config import settings
    monkeypatch.setattr(settings, "analytics_retention_days", 36500)
    monkeypatch.setattr(settings, "usage_retention_days", 36500)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield DojoDatabase(Path(tmpdir) / "analytics.db")


# --- Instrumentation ---

def test_instrument_html_rewrites_portal_links_and_adds_pixel():
    eid = "a" * 32
    html = (
        f'<html><body><a href="{BASE}/?item=x1&amp;y=2#item-x1">View</a>'
        f'<a href="{BASE}">Portal</a><a href="https://img.example.com/slip.jpg">img</a></body></html>'
    )
    out = instrument_html(html, eid, BASE + "/")

    assert f'href="{BASE}/t/c/{eid}?u=%2F%3Fitem%3Dx1%26y%3D2%23item-x1"' in out
    assert f'href="{BASE}/t/c/{eid}?u=%2F"' in out
    assert 'href="https://img.example.com/slip.jpg"' in out  # external links untouched
    assert out.index(f'{BASE}/t/o/{eid}.gif') < out.index("</body>")


def test_instrument_html_ignores_lookalike_hosts():
    out = instrument_html('<a href="https://dojo.example.test.evil.com/">x</a>', "b" * 32, BASE)
    assert "/t/c/" not in out


class _CaptureSMTP:
    sent = []

    def __init__(self, host, port, timeout=30):
        pass

    def starttls(self):
        pass

    def login(self, user, pwd):
        pass

    def sendmail(self, sender, to_addrs, msg_str):
        _CaptureSMTP.sent.append(msg_str)

    def quit(self):
        pass


def _html_part(msg_str: str) -> str:
    msg = email.message_from_string(msg_str)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode()
    raise AssertionError("no html part")


def _settings(**kw):
    return Settings(smtp_host="localhost", smtp_user="dojo@example.test", smtp_pass="x",
                    email_to="a@example.test, b@example.test", app_base_url=BASE, **kw)


def test_digest_send_is_tracked_and_recorded(db, monkeypatch):
    import smtplib
    _CaptureSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CaptureSMTP)

    briefing = Briefing(action_items=[ActionItem(summary="Field trip slip", context="c", item_id="it1")])
    assert Mailer(_settings(), db=db).send_digest(briefing, "text") is True

    rows = db.get_analytics_rows("2000-01-01", "2000-01-01")
    assert len(rows["sends"]) == 1
    send = rows["sends"][0]
    assert send["kind"] == "digest" and send["recipient_count"] == 2
    html = _html_part(_CaptureSMTP.sent[0])
    assert f"/t/o/{send['email_id']}.gif" in html
    assert f"/t/c/{send['email_id']}?u=" in html


def test_alert_send_is_tracked(db, monkeypatch):
    import smtplib
    from dojo.alert_evaluator import AlertDecision, UrgencyLevel
    _CaptureSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CaptureSMTP)

    decision = AlertDecision(item_id="m1", item_type="message", sender_or_author="Nurse", title="Fever",
                             body="fever", urgency=UrgencyLevel.IMMEDIATE, reason="health")
    Mailer(_settings(), db=db).send_urgent_alert(decision)
    sends = db.get_analytics_rows("2000-01-01", "2000-01-01")["sends"]
    assert [s["kind"] for s in sends] == ["alert"]
    assert f"/t/o/{sends[0]['email_id']}.gif" in _html_part(_CaptureSMTP.sent[0])


def test_tracking_can_be_disabled(db, monkeypatch):
    import smtplib
    _CaptureSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CaptureSMTP)

    Mailer(_settings(email_tracking=False), db=db).send_digest(Briefing(), "text")
    assert db.get_analytics_rows("2000-01-01", "2000-01-01")["sends"] == []
    assert "/t/o/" not in _html_part(_CaptureSMTP.sent[0])


def test_analytics_write_failure_never_blocks_send(monkeypatch):
    import smtplib
    _CaptureSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CaptureSMTP)

    class BrokenDB:
        def record_email_send(self, **kw):
            raise RuntimeError("firestore down")

    assert Mailer(_settings(), db=BrokenDB()).send_digest(Briefing(), "text") is True
    assert len(_CaptureSMTP.sent) == 1


def test_failed_smtp_send_is_not_recorded(db, monkeypatch):
    import smtplib

    class DownSMTP(_CaptureSMTP):
        def sendmail(self, *a):
            raise ConnectionError("smtp down")

    monkeypatch.setattr(smtplib, "SMTP", DownSMTP)
    with pytest.raises(ConnectionError):
        Mailer(_settings(), db=db).send_digest(Briefing(), "text")
    assert db.get_analytics_rows("2000-01-01", "2000-01-01")["sends"] == []


# --- Tracking endpoints ---

@pytest.fixture
def server_client(db, monkeypatch):
    from dojo import server
    monkeypatch.setattr(server, "db", db)
    return TestClient(server.app)


def test_open_pixel_records_event_and_is_uncacheable(server_client, db):
    eid = new_email_id()
    res = server_client.get(f"/t/o/{eid}.gif", headers={"User-Agent": "Mozilla/5.0 (via ggpht.com GoogleImageProxy)"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/gif"
    assert "no-store" in res.headers["cache-control"]
    assert res.content == analytics.PIXEL_GIF

    db.record_email_send(eid, "digest", "s", 2, 3, (NOW - timedelta(hours=1)).isoformat())
    events = db.get_analytics_rows("2000-01-01", "2000-01-01")["events"]
    assert [(e["event_type"], e["client"]) for e in events] == [("open", "gmail_proxy")]


def test_open_pixel_ignores_malformed_ids(server_client, db):
    assert server_client.get("/t/o/not-an-id.gif").status_code == 200
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM email_events").fetchone()[0] == 0


@pytest.mark.parametrize("u,expected", [
    ("/?item=abc#item-abc", "/?item=abc#item-abc"),
    ("https://evil.example.com/phish", "/"),
    ("//evil.example.com", "/"),
    (None, "/"),
])
def test_click_redirects_only_same_site(server_client, db, u, expected):
    eid = new_email_id()
    params = {"u": u} if u is not None else {}
    res = server_client.get(f"/t/c/{eid}", params=params, follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == expected
    with db.get_connection() as conn:
        row = conn.execute("SELECT event_type, target FROM email_events").fetchone()
    assert tuple(row) == ("click", expected)


def test_tracking_endpoints_are_public_when_auth_required(server_client, monkeypatch):
    from dojo.config import settings
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "dojo_access_token", "tok")
    eid = new_email_id()
    assert server_client.get(f"/t/o/{eid}.gif").status_code == 200
    assert server_client.get(f"/t/c/{eid}", params={"u": "/"}, follow_redirects=False).status_code == 302
    # ...but the summary isn't
    assert server_client.get("/api/analytics").status_code == 401
    assert server_client.get("/api/analytics", headers={"Authorization": "Bearer tok"}).status_code == 200


def test_api_analytics_endpoint(server_client, db):
    eid = new_email_id()
    db.record_email_send(eid, "digest", "s", 2, 3, datetime.now(timezone.utc).isoformat())
    server_client.get(f"/t/o/{eid}.gif")
    body = server_client.get("/api/analytics?days=7").json()
    assert body["window_days"] == 7
    assert body["email"]["by_kind"]["digest"]["sent"] == 1
    assert body["email"]["by_kind"]["digest"]["open_rate"] == 1.0
    assert body["caveats"]


# --- Usage counters ---

@pytest.mark.parametrize("method,path,channel", [
    ("GET", "/", "portal_view"),
    ("POST", "/mcp", "mcp"),
    ("GET", "/mcp", None),  # SSE stream, not a call
    ("POST", "/api/ask", "ask"),
    ("POST", "/api/sms/webhook", "sms"),
    ("GET", "/api/feed", "api"),
    ("GET", "/api/status", None),
    ("POST", "/api/cron/check-alerts", None),
    ("GET", "/api/analytics", None),
    ("GET", "/t/o/x.gif", None),
])
def test_usage_channel(method, path, channel):
    assert usage_channel(method, path) == channel


def _usage_app(recorded, enabled=True):
    async def ok(request):
        return PlainTextResponse("ok")

    async def denied(request):
        return PlainTextResponse("no", status_code=401)

    inner = Starlette(routes=[Route("/", ok), Route("/api/feed", ok), Route("/api/ask", denied, methods=["POST"])])
    return UsageMiddleware(inner, recorder=lambda day, ch: recorded.append((day, ch)),
                           enabled=lambda: enabled, run_inline=True)


def test_usage_middleware_counts_successful_requests_only():
    recorded = []
    client = TestClient(_usage_app(recorded))
    client.get("/")
    client.get("/api/feed")
    client.post("/api/ask")  # 401: not counted
    assert [ch for _, ch in recorded] == ["portal_view", "api"]


def test_usage_middleware_disabled():
    recorded = []
    TestClient(_usage_app(recorded, enabled=False)).get("/")
    assert recorded == []


def test_usage_recorder_failure_does_not_fail_request():
    app = UsageMiddleware(_usage_app([]).app, recorder=lambda d, c: 1 / 0, run_inline=True)
    assert TestClient(app).get("/").status_code == 200


# --- Summary maths ---

def _seed(store):
    """Three digests and one alert in the window, one old digest outside it."""
    sends = [
        ("d1", "digest", NOW - timedelta(days=1, hours=0)),
        ("d2", "digest", NOW - timedelta(days=2)),
        ("d3", "digest", NOW - timedelta(days=3)),
        ("a1", "alert", NOW - timedelta(hours=5)),
        ("old", "digest", NOW - timedelta(days=60)),
    ]
    for eid, kind, at in sends:
        store.record_email_send(eid, kind, "s", 2, 1, at.isoformat())
    d1 = NOW - timedelta(days=1)
    # d1 opened twice (first after 10 min) and clicked; d2 clicked with images blocked; d3 never opened
    store.record_email_event("d1", "open", client="gmail_proxy", created_at=(d1 + timedelta(minutes=10)).isoformat())
    store.record_email_event("d1", "open", client="other", created_at=(d1 + timedelta(hours=3)).isoformat())
    store.record_email_event("d1", "click", target="/?item=x#item-x", created_at=(d1 + timedelta(minutes=12)).isoformat())
    store.record_email_event("d2", "click", target="/", created_at=(NOW - timedelta(days=2) + timedelta(minutes=30)).isoformat())
    store.record_email_event("a1", "open", created_at=(NOW - timedelta(hours=5) + timedelta(minutes=2)).isoformat())
    store.record_email_event("old", "open", created_at=(NOW - timedelta(days=59)).isoformat())
    store.record_email_event("ghost", "open", created_at=NOW.isoformat())  # unknown id: ignored
    store.increment_usage("2026-11-15", "mcp")
    store.increment_usage("2026-11-15", "mcp")
    store.increment_usage("2026-11-16", "portal_view")
    store.increment_usage("2026-09-01", "mcp")  # outside window


def _summary(store):
    since = NOW - timedelta(days=30)
    rows = store.get_analytics_rows(since.isoformat(), "2026-10-17")
    return summarize(rows["sends"], rows["events"], rows["usage"], days=30, now=NOW)


def test_summary_rates_and_usage(db):
    _seed(db)
    s = _summary(db)
    digest = s["email"]["by_kind"]["digest"]
    assert digest["sent"] == 3
    assert digest["opened"] == 2  # d1 by pixel, d2 implied by the click
    assert digest["open_rate"] == round(2 / 3, 3)
    assert digest["clicked"] == 2
    assert digest["click_to_open_rate"] == 1.0
    assert digest["median_minutes_to_open"] == 10.0
    assert s["email"]["by_kind"]["alert"] == {
        "sent": 1, "opened": 1, "open_rate": 1.0, "clicked": 0, "click_rate": 0.0,
        "click_to_open_rate": 0.0, "total_clicks": 0, "median_minutes_to_open": 2.0,
    }
    assert s["email"]["sent"] == 4 and s["email"]["opened"] == 3
    assert s["email"]["top_click_targets"] == {"portal_item": 1, "portal_home": 1}
    assert s["email"]["opens_by_client"] == {"gmail_proxy": 1, "other": 2}
    # 20:10 and 23:00 UTC on Nov 15 -> 15:10 and 18:00 ET; alert open 15:02 UTC -> 10:02 ET
    assert s["email"]["opens_by_hour"] == {"10": 1, "15": 1, "18": 1}
    assert s["usage"]["totals"] == {"mcp": 2, "portal_view": 1}
    assert s["usage"]["daily"] == [{"day": "2026-11-15", "mcp": 2}, {"day": "2026-11-16", "portal_view": 1}]
    assert s["usage"]["busiest_weekday"] == "Sunday"


def test_summary_empty():
    s = summarize([], [], [], days=30, now=NOW)
    assert s["email"]["sent"] == 0 and s["email"]["open_rate"] is None
    assert s["usage"]["totals"] == {}


def test_firestore_parity(db):
    fdb = FirestoreDojoDatabase(project="test-project", client=MockFirestoreClient())
    _seed(db)
    _seed(fdb)
    assert _summary(fdb) == _summary(db)
    assert fdb.usage_col.document("2026-11-15").get().to_dict()["mcp"] == 2
