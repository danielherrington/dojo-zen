"""Retention limits for email-tracking and usage-analytics data (DAN-205)."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dojo.analytics import EXPIRE_FIELD, expire_at
from dojo.config import Settings, settings
from dojo.db import DojoDatabase
from dojo.firestore_db import FirestoreDojoDatabase

from test_firestore_db import MockFirestoreClient

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def fdb():
    return FirestoreDojoDatabase(project="test-project", client=MockFirestoreClient())


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield DojoDatabase(Path(tmpdir) / "retention.db")


def _doc(col, doc_id):
    return col.document(doc_id).get().to_dict()


# --- Configuration ---

def test_retention_defaults(monkeypatch):
    monkeypatch.delenv("ANALYTICS_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("USAGE_RETENTION_DAYS", raising=False)
    s = Settings()
    assert s.analytics_retention_days == 90
    assert s.usage_retention_days == 400


@pytest.mark.parametrize("raw,expected", [("30", 30), ("0", 90), ("-5", 90), ("ninety", 90)])
def test_retention_env_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("ANALYTICS_RETENTION_DAYS", raw)
    assert Settings().analytics_retention_days == expected


def test_expire_at_accepts_iso_day_and_naive():
    assert expire_at("2026-11-16T12:00:00+00:00", 90) == datetime(2027, 2, 14, 12, tzinfo=timezone.utc)
    assert expire_at("2026-11-16", 400) == datetime(2027, 12, 21, tzinfo=timezone.utc)
    assert expire_at(datetime(2026, 1, 1), 1) == datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert expire_at(None, 90) is None and expire_at("garbage", 90) is None


# --- New Firestore rows get expireAt ---

def test_new_firestore_rows_get_expire_at(fdb):
    sent = "2026-11-16T12:00:00+00:00"
    fdb.record_email_send("e1", "digest", "s", 2, 3, sent)
    fdb.record_email_event("e1", "open", created_at="2026-11-16T12:05:00+00:00")
    fdb.increment_usage("2026-11-16", "mcp")
    fdb.increment_usage("2026-11-16", "portal_view")

    assert _doc(fdb.email_send_col, "e1")[EXPIRE_FIELD] == datetime(2027, 2, 14, 12, tzinfo=timezone.utc)
    (event,) = [d.to_dict() for d in fdb.email_event_col.stream()]
    assert event[EXPIRE_FIELD] == datetime(2027, 2, 14, 12, 5, tzinfo=timezone.utc)
    usage = _doc(fdb.usage_col, "2026-11-16")
    assert usage[EXPIRE_FIELD] == datetime(2027, 12, 21, tzinfo=timezone.utc)
    assert usage["mcp"] == 1 and usage["portal_view"] == 1  # merge keeps counters


def test_firestore_expiry_respects_configuration(fdb, monkeypatch):
    monkeypatch.setattr(settings, "analytics_retention_days", 30)
    monkeypatch.setattr(settings, "usage_retention_days", 365)
    fdb.record_email_send("e1", "alert", "s", 1, 1, "2026-11-16T00:00:00+00:00")
    fdb.increment_usage("2026-11-16", "ask")
    assert _doc(fdb.email_send_col, "e1")[EXPIRE_FIELD] == datetime(2026, 12, 16, tzinfo=timezone.utc)
    assert _doc(fdb.usage_col, "2026-11-16")[EXPIRE_FIELD] == datetime(2027, 11, 16, tzinfo=timezone.utc)


def test_event_without_timestamp_expires_from_now(fdb):
    fdb.record_email_event("e1", "click", target="/")
    (event,) = [d.to_dict() for d in fdb.email_event_col.stream()]
    delta = event[EXPIRE_FIELD] - NOW
    assert timedelta(days=89, hours=23) < delta < timedelta(days=90, minutes=5)


def test_firestore_reads_hide_rows_past_expiry(fdb):
    """TTL deletion can lag ~24h; expired rows must not show up in the meantime."""
    fresh = (NOW - timedelta(days=1)).isoformat()
    stale = (NOW - timedelta(days=91)).isoformat()
    fdb.record_email_send("fresh", "digest", "s", 1, 1, fresh)
    fdb.record_email_send("stale", "digest", "s", 1, 1, stale)
    fdb.record_email_event("fresh", "open", created_at=fresh)
    fdb.record_email_event("stale", "open", created_at=stale)

    rows = fdb.get_analytics_rows((NOW - timedelta(days=365)).isoformat(), "2000-01-01")
    assert [s["email_id"] for s in rows["sends"]] == ["fresh"]
    assert [e["email_id"] for e in rows["events"]] == ["fresh"]
    assert all(EXPIRE_FIELD not in r for r in rows["sends"] + rows["events"])


# --- Backfill of rows written before DAN-205 ---

def _seed_legacy(fdb):
    """Docs as DAN-200 wrote them: no expireAt."""
    fdb.email_send_col.document("e1").set({"email_id": "e1", "kind": "digest", "sent_at": "2026-11-16T12:00:00+00:00"})
    fdb.email_event_col.document("ev1").set({"email_id": "e1", "event_type": "open", "created_at": "2026-11-16T13:00:00+00:00"})
    fdb.email_event_col.document("ev2").set({"email_id": "x", "event_type": "click"})  # no timestamp
    fdb.usage_col.document("2026-11-16").set({"day": "2026-11-16", "mcp": 4})


def test_backfill_dry_run_writes_nothing(fdb):
    _seed_legacy(fdb)
    counts = fdb.backfill_analytics_expiry(apply=False)
    assert counts == {"dojo_email_sends": 1, "dojo_email_events": 1, "dojo_usage": 1}
    assert EXPIRE_FIELD not in _doc(fdb.email_send_col, "e1")


def test_backfill_sets_expire_at_on_old_rows(fdb):
    _seed_legacy(fdb)
    fdb.backfill_analytics_expiry(apply=True)
    assert _doc(fdb.email_send_col, "e1")[EXPIRE_FIELD] == datetime(2027, 2, 14, 12, tzinfo=timezone.utc)
    assert _doc(fdb.email_event_col, "ev1")[EXPIRE_FIELD] == datetime(2027, 2, 14, 13, tzinfo=timezone.utc)
    assert _doc(fdb.usage_col, "2026-11-16")[EXPIRE_FIELD] == datetime(2027, 12, 21, tzinfo=timezone.utc)
    assert _doc(fdb.usage_col, "2026-11-16")["mcp"] == 4  # other fields untouched
    assert EXPIRE_FIELD not in _doc(fdb.email_event_col, "ev2")  # unknown age: skipped, not guessed

    # Idempotent: a second run finds nothing left to do
    assert fdb.backfill_analytics_expiry(apply=True) == {"dojo_email_sends": 0, "dojo_email_events": 0, "dojo_usage": 0}


def test_backfill_uses_configured_retention(fdb, monkeypatch):
    monkeypatch.setattr(settings, "analytics_retention_days", 7)
    _seed_legacy(fdb)
    fdb.backfill_analytics_expiry(apply=True)
    assert _doc(fdb.email_send_col, "e1")[EXPIRE_FIELD] == datetime(2026, 11, 23, 12, tzinfo=timezone.utc)


def test_backfill_leaves_new_rows_alone(fdb, monkeypatch):
    fdb.record_email_send("e2", "digest", "s", 1, 1, "2026-11-16T12:00:00+00:00")
    before = _doc(fdb.email_send_col, "e2")[EXPIRE_FIELD]
    monkeypatch.setattr(settings, "analytics_retention_days", 7)
    assert fdb.backfill_analytics_expiry(apply=True)["dojo_email_sends"] == 0
    assert _doc(fdb.email_send_col, "e2")[EXPIRE_FIELD] == before


# --- SQLite purge (no TTL in SQLite) ---

def _seed_sqlite(db):
    for eid, age in (("recent", 10), ("edge", 89), ("old", 91), ("ancient", 300)):
        at = (NOW - timedelta(days=age)).isoformat()
        db.record_email_send(eid, "digest", "s", 1, 1, at)
        db.record_email_event(eid, "open", created_at=at)
    for age in (5, 399, 401):
        db.increment_usage((NOW - timedelta(days=age)).date().isoformat(), "mcp")


def _count(db, table):
    with db.get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_sqlite_removes_old_rows_and_keeps_recent(db):
    _seed_sqlite(db)
    rows = db.get_analytics_rows("2000-01-01", "2000-01-01")
    assert sorted(s["email_id"] for s in rows["sends"]) == ["edge", "recent"]
    assert sorted(e["email_id"] for e in rows["events"]) == ["edge", "recent"]
    assert len(rows["usage"]) == 2  # 5 and 399 days kept, 401 dropped
    assert _count(db, "email_sends") == 2 and _count(db, "email_events") == 2 and _count(db, "usage_daily") == 2


def test_sqlite_purge_respects_configuration(db, monkeypatch):
    _seed_sqlite(db)
    monkeypatch.setattr(settings, "analytics_retention_days", 30)
    monkeypatch.setattr(settings, "usage_retention_days", 30)
    db.purge_expired_analytics()
    # Only the 10-day-old email and the 5-day-old counter survive a 30-day limit
    assert _count(db, "email_sends") == 1
    assert _count(db, "email_events") == 1
    assert _count(db, "usage_daily") == 1


def test_summary_reports_retention():
    from dojo.analytics import summarize
    assert summarize([], [], [])["retention"] == {"email_events_days": 90, "usage_counters_days": 400}
