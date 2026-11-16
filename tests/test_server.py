"""Unit tests for the FastAPI web server."""

import pytest
from fastapi.testclient import TestClient

from dojo.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Dojo Zen" in response.text
    assert "Ask Dojo" in response.text


def test_api_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "stats" in data


def test_api_ask(client):
    response = client.post("/api/ask", json={"query": "homework"})
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert "sources" in data


def test_api_feed(client):
    response = client.get("/api/feed?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)


def test_api_briefing(client):
    response = client.get("/api/briefing")
    assert response.status_code == 200
    data = response.json()
    assert "briefing" in data
    assert "action_items" in data["briefing"]


def test_cron_unauthorized(client):
    response = client.post("/api/cron/check-alerts")
    assert response.status_code == 401

    response = client.post("/api/cron/daily-digest")
    assert response.status_code == 401


def test_cron_authorized_query_secret(client, monkeypatch):
    from dojo.config import settings
    monkeypatch.setattr(settings, "cron_secret", "test-secret-123")

    # Mock MessageMonitor and DojoClient to prevent real network calls
    class MockMonitor:
        def __init__(self, *args, **kwargs): pass
        def check_once(self, *args, **kwargs): return {"checked": 5, "urgent_alerts_sent": 0}

    monkeypatch.setattr("dojo.client.DojoClient.is_authenticated", lambda self: True)
    monkeypatch.setattr("dojo.monitor.MessageMonitor", MockMonitor)

    res = client.post("/api/cron/check-alerts?secret=test-secret-123")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert "urgent_alerts_sent" in res.json()


def test_cron_authorized_bearer_header(client, monkeypatch):
    from dojo.config import settings
    monkeypatch.setattr(settings, "cron_secret", "test-secret-123")
    monkeypatch.setattr("dojo.mailer.dispatch_daily_briefing", lambda *args, **kwargs: {"status": "skipped"})
    monkeypatch.setattr("dojo.schedule.digest_schedule_decision", lambda *a, **k: (True, "Scheduled send window"))

    res = client.post(
        "/api/cron/daily-digest",
        headers={"Authorization": "Bearer test-secret-123"}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_cron_daily_digest_skips_outside_send_window(client, monkeypatch):
    from dojo.config import settings
    monkeypatch.setattr(settings, "cron_secret", "test-secret-123")
    monkeypatch.setattr("dojo.schedule.digest_schedule_decision", lambda *a, **k: (False, "No briefing on Saturdays"))

    called = []
    monkeypatch.setattr("dojo.mailer.dispatch_daily_briefing", lambda *a, **k: called.append(1) or {"status": "sent"})

    res = client.post("/api/cron/daily-digest?secret=test-secret-123")
    assert res.status_code == 200
    assert res.json() == {"status": "skipped", "reason": "No briefing on Saturdays"}
    assert called == []


def test_cron_daily_digest_force_overrides_send_window(client, monkeypatch):
    from dojo.config import settings
    monkeypatch.setattr(settings, "cron_secret", "test-secret-123")
    monkeypatch.setattr("dojo.schedule.digest_schedule_decision", lambda *a, **k: (False, "No briefing on Saturdays"))
    monkeypatch.setattr("dojo.client.DojoClient.is_authenticated", lambda self: False)
    monkeypatch.setattr("dojo.mailer.dispatch_daily_briefing", lambda *a, **k: {"status": "sent"})

    res = client.post("/api/cron/daily-digest?secret=test-secret-123&force=true")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
