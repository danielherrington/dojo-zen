"""Unit tests for the Alert Evaluator and Urgency Classification."""

import tempfile
from pathlib import Path
import pytest

from dojo.config import Settings
from dojo.db import DojoDatabase
from dojo.alert_evaluator import AlertEvaluator, UrgencyLevel
from dojo.monitor import MessageMonitor
from dojo.mailer import Mailer


def test_health_emergency_classification():
    evaluator = AlertEvaluator()

    # Nurse / fever
    msg1 = {
        "id": "msg_fever",
        "sender_name": "Nurse Kelly",
        "body": "Hi, Leo came to the nurse's office with a fever. Please come pick him up.",
        "message_timestamp": "2026-09-14T11:00:00Z"
    }
    decision1 = evaluator.evaluate_message(msg1)
    assert decision1.urgency == UrgencyLevel.IMMEDIATE
    assert "Health" in decision1.title

    # Injury / hurt
    msg2 = {
        "id": "msg_hurt",
        "sender_name": "Ms. Jenkins",
        "body": "Leo got hurt on the playground and we put an ice pack on his knee.",
        "message_timestamp": "2026-09-14T11:30:00Z"
    }
    decision2 = evaluator.evaluate_message(msg2)
    assert decision2.urgency == UrgencyLevel.IMMEDIATE


def test_logistics_and_call_request():
    evaluator = AlertEvaluator()

    # Same-day bus delay
    feed1 = {
        "id": "feed_bus",
        "author_name": "Transportation",
        "header": "Bus Update",
        "content_text": "Bus 12 is delayed by 30 minutes today due to road work.",
        "item_timestamp": "2026-09-14T14:45:00Z"
    }
    decision1 = evaluator.evaluate_feed_item(feed1)
    assert decision1.urgency == UrgencyLevel.IMMEDIATE

    # Teacher call asap
    msg_call = {
        "id": "msg_call",
        "sender_name": "Mr. Roberts",
        "body": "Please call me as soon as possible regarding an incident in class today.",
        "message_timestamp": "2026-09-14T13:15:00Z"
    }
    decision_call = evaluator.evaluate_message(msg_call)
    assert decision_call.urgency == UrgencyLevel.IMMEDIATE


def test_routine_held_for_daily_recap():
    evaluator = AlertEvaluator()

    # Routine homework / compliment
    msg = {
        "id": "msg_routine",
        "sender_name": "Ms. Jenkins",
        "body": "Leo did awesome in reading group today! Homework is reading 15 minutes tonight.",
        "message_timestamp": "2026-09-14T15:00:00Z"
    }
    decision = evaluator.evaluate_message(msg)
    assert decision.urgency == UrgencyLevel.ROUTINE
    assert "queued for daily recap" in decision.reason.lower()


def test_alert_mailer_rendering():
    settings = Settings()
    mailer = Mailer(settings)

    evaluator = AlertEvaluator()
    msg = {
        "id": "msg_urgent_test",
        "sender_name": "Nurse Sarah",
        "body": "Leo is in the clinic with a mild fever. Please call when you arrive.",
        "message_timestamp": "2026-09-14T11:00:00Z"
    }
    decision = evaluator.evaluate_message(msg)
    html = mailer.render_alert_html(decision)

    assert "Urgent ClassDojo Alert" in html
    assert "Nurse Sarah" in html
    assert "fever" in html
    assert "Check on your child" in html


def test_monitor_deduplication():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DojoDatabase(Path(tmpdir) / "test.db")
        settings = Settings()
        settings.dojo_db_path = Path(tmpdir) / "test.db"

        # Insert urgent message
        db.upsert_messages([
            {
                "id": "m1",
                "sender_name": "Nurse Sarah",
                "body": "Leo is in clinic with fever.",
                "message_timestamp": "2026-09-14T11:00:00Z"
            }
        ])

        monitor = MessageMonitor(settings, db)
        alerts1 = monitor.check_once(send_email=False)
        assert len(alerts1) == 1
        assert alerts1[0].urgency == UrgencyLevel.IMMEDIATE

        # Second check should NOT re-alert
        alerts2 = monitor.check_once(send_email=False)
        assert len(alerts2) == 0
