"""herrington.usage.v1 events, funnels, triage stats and the analytics report (DAN-233)."""

import io
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from dojo import usage_v1
from dojo.alert_evaluator import AlertEvaluator, UrgencyLevel
from dojo.analytics import (
    UsageMiddleware,
    build_report,
    format_report,
    instrument_html,
    new_email_id,
    summarize,
)
from dojo.config import Settings, settings
from dojo.db import DojoDatabase
from dojo.firestore_db import FirestoreDojoDatabase

from test_firestore_db import MockFirestoreClient

NOW = datetime.now(timezone.utc).replace(microsecond=0)
FEVER = "Isabella is in the nurse's office with a fever. Please come pick her up."
POLICY = "Reminder: please keep your child home if they are sick."


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield DojoDatabase(Path(tmpdir) / "v1.db")


def _events(text: str):
    return [json.loads(line) for line in text.splitlines() if '"herrington.usage.v1"' in line]


# --- Schema, env, actor, kill switch ---

def test_emit_writes_v1_line():
    buf = io.StringIO()
    rec = usage_v1.emit("feature_used", "web", feature="ask", props={"result_count": 2}, stream=buf)
    (line,) = _events(buf.getvalue())
    assert line == rec
    assert line["schema"] == "herrington.usage.v1" and line["app"] == "dojo-zen"
    assert line["env"] == "test"  # tests never mix into prod
    assert line["event"] == "feature_used" and line["channel"] == "web" and line["feature"] == "ask"
    assert line["props"] == {"result_count": 2}
    assert line["ts"].endswith("Z") and "actor" not in line


def test_kill_switch_turns_everything_off(monkeypatch):
    monkeypatch.setenv("ANALYTICS", "off")
    s = Settings()
    assert not s.analytics_enabled and not s.email_tracking and not s.usage_analytics
    monkeypatch.setattr(settings, "analytics_enabled", False)
    buf = io.StringIO()
    assert usage_v1.emit("feature_used", "web", stream=buf) is None
    assert buf.getvalue() == ""


def test_env_defaults(monkeypatch):
    monkeypatch.delenv("ANALYTICS_ENV", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert Settings().analytics_env == "dev"
    monkeypatch.setenv("K_SERVICE", "dojo-zen")
    assert Settings().analytics_env == "prod"


def test_actor_hash_is_salted_and_never_raw():
    h = usage_v1.actor_hash("secret-token", salt="s1")
    assert len(h) == 16 and "secret" not in h
    assert h == usage_v1.actor_hash("secret-token", salt="s1")
    assert h != usage_v1.actor_hash("secret-token", salt="s2")
    assert usage_v1.actor_hash("secret-token", salt="") is None  # no salt -> no actor
    assert usage_v1.actor_hash(None, salt="s1") is None


@pytest.mark.parametrize("channel,ua,expected", [
    ("mcp", "Claude-User/1.0", "mcp:claude"),
    ("mcp", "Cursor/0.48 (darwin)", "mcp:cursor"),
    ("mcp", "python-httpx/0.27", "mcp:other"),
    ("portal_view", None, "web"),
    ("ask", None, "web"),
    ("api", None, "web"),
    ("sms", "TwilioProxy/1.1", "sms"),
])
def test_v1_channel(channel, ua, expected):
    assert usage_v1.v1_channel(channel, ua) == expected


def test_middleware_emits_feature_used_with_hashed_actor(monkeypatch, capsys):
    monkeypatch.setattr(settings, "analytics_salt", "pepper")

    async def ok(request):
        return PlainTextResponse("ok")

    app = UsageMiddleware(Starlette(routes=[Route("/mcp", ok, methods=["POST"])]), recorder=lambda d, c: None,
                          run_inline=True)
    TestClient(app).post("/mcp", headers={"Authorization": "Bearer fam-token", "User-Agent": "Claude-User/1.0"})
    (ev,) = _events(capsys.readouterr().out)
    assert ev["event"] == "feature_used" and ev["feature"] == "mcp" and ev["channel"] == "mcp:claude"
    assert ev["actor"] == usage_v1.actor_hash("fam-token", salt="pepper")
    assert "fam-token" not in json.dumps(ev)


# --- Triage categories and false-alarm guards ---

@pytest.mark.parametrize("body,category,urgency,guards", [
    (FEVER, "health", UrgencyLevel.IMMEDIATE, []),
    ("Early dismissal today at noon due to a water main break.", "logistics", UrgencyLevel.IMMEDIATE, []),
    ("Please call me as soon as possible.", "teacher_request", UrgencyLevel.IMMEDIATE, []),
    ("Isabella had a great day in art!", "routine", UrgencyLevel.ROUTINE, []),
    (POLICY, "routine", UrgencyLevel.ROUTINE, ["policy_reminder"]),
    ("Nothing urgent, just a quick note that Isabella had a great day.", "routine", UrgencyLevel.ROUTINE, ["negation"]),
    ("Please return the field trip permission slip asap.", "routine", UrgencyLevel.ROUTINE, ["paperwork"]),
    ("No fever, but she vomited twice and is pale. Can someone come get her?", "health", UrgencyLevel.IMMEDIATE, ["negation"]),
])
def test_message_triage_category_and_guards(body, category, urgency, guards):
    d = AlertEvaluator().evaluate_message({"id": "m", "body": body})
    assert (d.category, d.urgency, d.guards) == (category, urgency, guards)


def test_feed_triage_categories():
    ev = AlertEvaluator()
    assert ev.evaluate_feed_item({"id": "f", "content_text": "Early dismissal today at 12:30 due to a water main break."}).category == "school_closure"
    assert ev.evaluate_feed_item({"id": "f", "content_text": "Field day photos!"}).category == "routine"


def test_triage_counter_names():
    assert usage_v1.triage_counter_names("routine", ["policy_reminder"], alerted=False) == [
        "triage__routine", "guard__policy_reminder", "guard__prevented_alert"]
    assert usage_v1.triage_counter_names("health", ["negation"], alerted=True) == ["triage__health", "guard__negation"]


def test_record_triage_counts_and_logs_no_text(db, capsys):
    d = AlertEvaluator().evaluate_message({"id": "m1", "sender_name": "Ms. Alonso", "body": POLICY})
    usage_v1.record_triage(db, d, day="2026-11-17")
    (ev,) = _events(capsys.readouterr().out)
    assert ev["event"] == "triage" and ev["channel"] == "cron"
    assert ev["props"] == {"category": "routine", "urgency": "routine", "item_type": "message", "guards": ["policy_reminder"]}
    dumped = json.dumps(ev).lower()
    assert "keep your child home" not in dumped and "alonso" not in dumped

    (row,) = db.get_analytics_rows("2000-01-01", "2000-01-01")["usage"]
    assert row["triage__routine"] == 1 and row["guard__policy_reminder"] == 1 and row["guard__prevented_alert"] == 1


def test_monitor_records_triage_once_per_item(db):
    from dojo.monitor import MessageMonitor
    db.upsert_messages([{"id": "m_pol", "sender_name": "Nurse", "body": POLICY,
                         "message_timestamp": NOW.isoformat()}])
    monitor = MessageMonitor(Settings(), db)
    monitor.check_once(send_email=False)
    monitor.check_once(send_email=False)
    usage = db.get_analytics_rows("2000-01-01", "2000-01-01")["usage"]
    assert sum(r.get("triage__routine", 0) for r in usage) == 1


def test_summary_separates_triage_from_usage():
    usage = [{"day": NOW.date().isoformat(), "mcp": 3, "triage__health": 2, "triage__routine": 5,
              "guard__negation": 4, "guard__prevented_alert": 3}]
    s = summarize([], [], usage, days=7, now=NOW)
    assert s["usage"]["totals"] == {"mcp": 3}
    assert s["triage"] == {"categories": {"routine": 5, "health": 2},
                           "false_alarm_guards": {"negation": 4}, "alerts_prevented_by_guards": 3}


# --- Funnels ---

def _seed_funnels(store):
    t = (NOW - timedelta(days=1)).isoformat()
    for eid, kind in (("a1", "alert"), ("a2", "alert"), ("a3", "alert"), ("d1", "digest"), ("d2", "digest")):
        store.record_email_send(eid, kind, "s", 2, 1, t)
    store.record_email_event("a1", "open", created_at=t)
    store.record_email_event("a1", "click", target="/?item=x#item-x", created_at=t)  # action taken
    store.record_email_event("a2", "click", target="/", created_at=t)  # opened (via click) but no action
    store.record_email_event("d1", "open", created_at=t)
    store.record_email_event("d1", "click", target="/", created_at=t)
    store.record_email_event("d2", "open", created_at=t)


def _steps(funnel):
    return [(s["step"], s["count"], s["from_previous"]) for s in funnel]


@pytest.mark.parametrize("make", [
    lambda: DojoDatabase(Path(tempfile.mkdtemp()) / "f.db"),
    lambda: FirestoreDojoDatabase(project="t", client=MockFirestoreClient()),
])
def test_funnels_sqlite_and_firestore(make):
    store = make()
    _seed_funnels(store)
    f = build_report(store, days=7, now=NOW)["funnels"]
    assert _steps(f["alert"]) == [("sent", 3, None), ("opened", 2, 0.667), ("action_taken", 1, 0.5)]
    assert _steps(f["digest"]) == [("sent", 2, None), ("opened", 2, 1.0), ("link_clicked", 1, 0.5)]
    assert f["alert"][2]["from_start"] == 0.333


def test_instrument_html_tags_funnel_kind():
    eid = "c" * 32
    out = instrument_html('<body><a href="https://d.test/?item=1">x</a></body>', eid, "https://d.test", kind="alert")
    assert f"/t/c/{eid}?u=%2F%3Fitem%3D1&amp;k=alert" in out
    assert f"/t/o/{eid}.gif?k=alert" in out
    assert "k=" not in instrument_html('<body><a href="https://d.test/">x</a></body>', eid, "https://d.test", kind="bogus")


def test_tracking_endpoints_emit_funnel_steps(db, monkeypatch, capsys):
    from dojo import server
    monkeypatch.setattr(server, "db", db)
    client = TestClient(server.app)
    eid = new_email_id()
    client.get(f"/t/o/{eid}.gif", params={"k": "alert"})
    client.get(f"/t/c/{eid}", params={"u": "/?item=abc#item-abc", "k": "alert"}, follow_redirects=False)
    client.get(f"/t/c/{eid}", params={"u": "/", "k": "digest"}, follow_redirects=False)
    steps = [(e["funnel"], e["step"]) for e in _events(capsys.readouterr().out) if e["event"] == "funnel_step"]
    assert steps == [("alert", "opened"), ("alert", "action_taken"), ("digest", "link_clicked")]


def test_send_emits_sent_step_without_subject(db, monkeypatch, capsys):
    import smtplib

    class SMTP:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, *a): pass
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    from dojo.alert_evaluator import AlertDecision
    from dojo.mailer import Mailer
    s = Settings(smtp_host="localhost", smtp_user="u@example.test", smtp_pass="x", email_to="a@example.test")
    decision = AlertDecision(item_id="m", item_type="message", sender_or_author="Nurse Kelly", title="Isabella fever",
                             body=FEVER, urgency=UrgencyLevel.IMMEDIATE, reason="health", category="health")
    Mailer(s, db=db).send_urgent_alert(decision)
    (ev,) = [e for e in _events(capsys.readouterr().out) if e["event"] == "funnel_step"]
    assert (ev["funnel"], ev["step"], ev["channel"]) == ("alert", "sent", "email")
    assert set(ev["props"]) == {"email_id", "recipient_count", "item_count"}
    assert "isabella" not in json.dumps(ev).lower() and "actor" not in ev


# --- analytics report ---

def test_format_report_sections(db):
    _seed_funnels(db)
    db.increment_usage(NOW.date().isoformat(), "mcp")
    db.increment_usage(NOW.date().isoformat(), "triage__health")
    text = format_report(build_report(db, days=7, now=NOW))
    for heading in ("Daily activity by channel", "Funnels", "Features by use", "Alert triage", "Not measured yet"):
        assert heading in text
    assert "action_taken" in text and "link_clicked" in text and "health" in text


def test_cli_analytics_report(db, monkeypatch, capsys):
    from dojo import cli
    _seed_funnels(db)
    monkeypatch.setattr(cli, "get_db", lambda: db)
    monkeypatch.setattr(sys, "argv", ["dojo", "analytics", "report", "--days", "7", "--json"])
    cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["window_days"] == 7
    assert report["funnels"]["alert"][0]["count"] == 3
    assert report["gaps"]
