"""Failed emergency alerts are retried until delivered (DAN-163)."""

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dojo.config import Settings
from dojo.db import DojoDatabase
from dojo.firestore_db import FirestoreDojoDatabase
from dojo.monitor import MAX_ALERT_ATTEMPTS, MessageMonitor

from test_firestore_db import MockFirestoreClient

NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
FEVER_MSG = {
    "id": "m_fever",
    "sender_name": "Nurse Kelly",
    "body": "Isabella is in the nurse's office with a fever. Please come pick her up.",
    "message_timestamp": (NOW - timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
}


class FlakyMailer:
    """Records send attempts; raises while ``fail`` is True."""

    def __init__(self, fail: bool = True):
        self.fail = fail
        self.sent = []
        self.attempts = 0

    def send_urgent_alert(self, decision, to_email=None):
        self.attempts += 1
        if self.fail:
            raise ConnectionError("SMTP temporarily unavailable")
        self.sent.append(decision.item_id)
        return True


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield DojoDatabase(Path(tmpdir) / "test.db")


def _monitor(db, mailer):
    settings = Settings()
    settings.email_to = "family@example.test"
    monitor = MessageMonitor(settings, db)
    monitor.mailer = mailer
    return monitor


def test_failed_alert_is_retried_and_marked_sent(db):
    db.upsert_messages([FEVER_MSG])
    mailer = FlakyMailer(fail=True)
    monitor = _monitor(db, mailer)

    first = monitor.check_once(send_email=True, now=NOW)
    assert len(first) == 1
    assert first[0].delivery_status.startswith("failed")
    assert db.get_alert("m_fever")["sent_status"].startswith("failed")

    mailer.fail = False
    second = monitor.check_once(send_email=True, now=NOW + timedelta(minutes=10))
    assert [d.item_id for d in second] == ["m_fever"]
    assert second[0].delivery_status == "sent"
    assert second[0].is_retry is True
    assert mailer.sent == ["m_fever"]

    alert = db.get_alert("m_fever")
    assert alert["sent_status"] == "sent"
    assert alert["attempts"] == 2


def test_sent_alert_is_never_resent(db):
    db.upsert_messages([FEVER_MSG])
    mailer = FlakyMailer(fail=False)
    monitor = _monitor(db, mailer)

    monitor.check_once(send_email=True, now=NOW)
    again = monitor.check_once(send_email=True, now=NOW + timedelta(minutes=10))
    assert again == []
    assert mailer.sent == ["m_fever"]


def test_retry_stops_at_attempt_cap(db):
    db.upsert_messages([FEVER_MSG])
    mailer = FlakyMailer(fail=True)
    monitor = _monitor(db, mailer)

    for i in range(MAX_ALERT_ATTEMPTS + 3):
        monitor.check_once(send_email=True, now=NOW + timedelta(minutes=10 * i))

    assert mailer.attempts == MAX_ALERT_ATTEMPTS
    assert db.get_alert("m_fever")["attempts"] == MAX_ALERT_ATTEMPTS
    assert db.get_failed_alerts(max_attempts=MAX_ALERT_ATTEMPTS) == {"messages": [], "feed_items": []}


def test_stale_emergency_is_not_retried(db):
    db.upsert_messages([FEVER_MSG])
    mailer = FlakyMailer(fail=True)
    monitor = _monitor(db, mailer)
    monitor.check_once(send_email=True, now=NOW)

    mailer.fail = False
    later = monitor.check_once(send_email=True, now=NOW + timedelta(hours=25))
    assert later == []
    assert mailer.sent == []


def test_dry_run_is_not_treated_as_failure(db):
    db.upsert_messages([FEVER_MSG])
    mailer = FlakyMailer(fail=False)
    monitor = _monitor(db, mailer)

    monitor.check_once(send_email=False, now=NOW)
    assert db.get_alert("m_fever")["sent_status"] == "dry_run"
    assert monitor.check_once(send_email=True, now=NOW) == []
    assert mailer.attempts == 0


def test_failed_feed_alert_is_retried(db):
    db.upsert_feed_items([{
        "id": "f_closure",
        "senderName": "North Beach Elementary",
        "content_text": "Early dismissal today at 12:30 due to a water main break.",
        "item_timestamp": (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }])
    mailer = FlakyMailer(fail=True)
    monitor = _monitor(db, mailer)
    monitor.check_once(send_email=True, now=NOW)

    mailer.fail = False
    retried = monitor.check_once(send_email=True, now=NOW + timedelta(minutes=10))
    assert [d.item_id for d in retried] == ["f_closure"]
    assert db.get_alert("f_closure")["sent_status"] == "sent"


def test_sqlite_migration_adds_attempts_column():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, item_type TEXT NOT NULL,
                sender_or_author TEXT, title TEXT, reason TEXT NOT NULL, urgency_level TEXT NOT NULL,
                sent_at TEXT NOT NULL, recipient_email TEXT, sent_status TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO alerts (item_id, item_type, reason, urgency_level, sent_at, sent_status) "
            "VALUES ('old', 'message', 'r', 'immediate', '2026-01-01', 'failed: x')"
        )
        conn.commit()
        conn.close()

        db = DojoDatabase(path)
        assert db.get_alert("old")["attempts"] == 1


def test_firestore_failed_alert_parity():
    fdb = FirestoreDojoDatabase(project="test-project", client=MockFirestoreClient())
    fdb.upsert_messages([FEVER_MSG])
    msg_id = "m_fever"

    common = dict(item_type="message", sender_or_author="Nurse Kelly", title="t",
                  reason="r", urgency_level="immediate", recipient_email="family@example.test")
    fdb.record_alert(item_id=msg_id, sent_status="failed: smtp down", **common)
    assert fdb.get_alert(msg_id)["attempts"] == 1

    failed = fdb.get_failed_alerts(max_attempts=5)
    assert [m["id"] for m in failed["messages"]] == [msg_id]
    assert failed["messages"][0]["alert_attempts"] == 1

    fdb.record_alert(item_id=msg_id, sent_status="sent", **common)
    assert fdb.get_alert(msg_id)["attempts"] == 2
    assert fdb.get_failed_alerts(max_attempts=5)["messages"] == []

    # Attempt cap is honoured
    fdb.record_alert(item_id="m_capped", sent_status="failed: x", **common)
    for _ in range(4):
        fdb.record_alert(item_id="m_capped", sent_status="failed: x", **common)
    assert fdb.get_alert("m_capped")["attempts"] == 5
    assert all(m["id"] != "m_capped" for m in fdb.get_failed_alerts(max_attempts=5)["messages"])
