"""DAN-395: read-only school email sync (synthetic emails only; no Gmail or Gemini calls)."""

import base64
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from dojo import logistics, school_email
from dojo.db import DojoDatabase

RECEIVED = datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)
BODY = ("Dear families,\nThere is no school on Wednesday, November 26 for Thanksgiving break. "
        "Field trip forms are due Dec 3. Ms. Example's class sings at the winter concert on December 11.\n"
        "Our gala was held October 2.")


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _msg(mid="m1", body=BODY, subject="Weekly update"):
    return {"id": mid, "subject": subject, "received": RECEIVED, "text": body}


@pytest.fixture
def db(tmp_path):
    return DojoDatabase(tmp_path / "t.db")


@pytest.fixture
def lehrman_family(monkeypatch):
    monkeypatch.setenv("FAMILY_SCHOOLS", json.dumps([
        {"child": "Kid A", "school": "Test Elementary", "source": "dojo"},
        {"child": "Kid B", "school": "Test Academy", "source": "lehrman"}]))
    for n in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.setenv(n, "test-value")


def test_body_text_prefers_plain_and_strips_html():
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"data": _b64("<p>Hi<script>x()</script></p><b>Bye</b>")}}]}
    assert school_email.body_text(payload) == "Hi\nBye"
    payload["parts"].append({"mimeType": "text/plain", "body": {"data": _b64("Plain wins")}})
    assert school_email.body_text(payload) == "Plain wins"


def test_parse_message_keeps_subject_date_text_only():
    msg = {"id": "abc", "internalDate": str(int(RECEIVED.timestamp() * 1000)), "payload": {
        "mimeType": "text/plain", "body": {"data": _b64("Hello")},
        "headers": [{"name": "Subject", "value": "News"}, {"name": "From", "value": "Teacher <t@school.test>"}]}}
    out = school_email.parse_message(msg)
    assert out == {"id": "abc", "subject": "News", "received": RECEIVED, "text": "Hello"}


def test_rule_extraction_finds_future_dates_and_types():
    items = school_email.extract_with_rules(_msg())
    by_date = {i["date"]: i for i in items}
    assert by_date["2026-11-26"]["type"] == "day_off"
    assert by_date["2026-12-03"]["type"] == "deadline"
    assert "2026-12-11" in by_date
    assert "2026-10-02" not in by_date  # past dates dropped
    assert all(len(i["title"]) <= school_email.MAX_TITLE for i in items)


def test_rule_extraction_rolls_january_into_next_year():
    msg = dict(_msg(body="Classes resume January 5."), received=datetime(2026, 12, 15, tzinfo=timezone.utc))
    assert school_email.extract_with_rules(msg)[0]["date"] == "2027-01-05"


def test_clean_items_rejects_bad_dates_and_types():
    out = school_email._clean_items([
        {"date": "2026-11-26", "title": "No school", "type": "day_off"},
        {"date": "someday", "title": "x", "type": "event"},
        {"date": "2026-11-30", "title": "  ", "type": "event"},
        {"date": "2026-12-01", "title": "Concert", "type": "party"},
    ], RECEIVED.date())
    assert out == [{"date": "2026-11-26", "title": "No school", "type": "day_off"},
                   {"date": "2026-12-01", "title": "Concert", "type": "event"}]


def test_sync_stores_items_and_skips_seen_messages(db):
    calls = []

    def fetch(query, skip=()):
        calls.append((query, set(skip)))
        return [m for m in (_msg("m1"), _msg("m2", body="Nothing dated here.")) if m["id"] not in set(skip)]

    first = school_email.sync(db, fetch=fetch, extract=school_email.extract_with_rules)
    assert first["new_messages"] == 2 and first["items"] == 3
    again = school_email.sync(db, fetch=fetch, extract=school_email.extract_with_rules)
    assert again["new_messages"] == 0
    assert calls[1][1] == {"m1", "m2"}
    assert calls[0][0] == school_email.DEFAULT_QUERY

    stored = db.get_school_items("lehrman")
    assert [i["date"] for i in stored] == ["2026-11-26", "2026-12-03", "2026-12-11"]
    assert all(BODY not in json.dumps(i) for i in stored)  # titles only, never bodies


def test_school_today_and_upcoming_read_email_items(db, lehrman_family, monkeypatch):
    school_email.sync(db, fetch=lambda q, skip=(): [_msg()], extract=school_email.extract_with_rules)
    monkeypatch.setattr(logistics, "family_schools", lambda _db: [
        {"child": "Kid B", "school": "Test Academy", "source": "lehrman"}])
    (kid,) = logistics.school_today(db, "2026-11-26")
    assert kid["status"] == "ok" and kid["events"][0]["type"] == "day_off"
    (due,) = logistics.school_today(db, "2026-12-02")
    assert due["action_items"][0]["due"] == "2026-12-03"


def test_lehrman_not_connected_without_gmail(monkeypatch):
    for n in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(n, raising=False)
    assert logistics.connected_sources() == {"dojo"}


def test_cron_endpoint_requires_secret_and_reports_not_configured(monkeypatch):
    from dojo.config import settings
    from dojo.server import app

    monkeypatch.setattr(settings, "cron_secret", "cron-test")
    for n in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(n, raising=False)
    client = TestClient(app)
    assert client.post("/api/cron/sync-school-email").status_code == 401
    res = client.post("/api/cron/sync-school-email", headers={"Authorization": "Bearer cron-test"})
    assert res.status_code == 200 and res.json() == {"status": "not_configured"}
