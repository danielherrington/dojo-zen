"""Unit tests for SQLite database layer."""

import tempfile
from pathlib import Path
import pytest
from dojo.db import DojoDatabase


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_dojo.db"
        yield DojoDatabase(db_path)


def test_upsert_children_and_classes(temp_db):
    temp_db.upsert_child("child_1", "Leo", grade="1st")
    temp_db.upsert_class("class_1", "Room 101", teacher_name="Ms. Jenkins", child_id="child_1")

    stats = temp_db.get_stats()
    assert stats["children_count"] == 1
    assert stats["classes_count"] == 1


def test_upsert_feed_items_and_undigested(temp_db):
    items = [
        {
            "id": "post_1",
            "author_name": "Ms. Jenkins",
            "content_text": "Please bring a water bottle tomorrow.",
            "time": "2026-09-14T08:00:00Z",
            "attachments": []
        },
        {
            "id": "post_2",
            "author_name": "Principal Smith",
            "content_text": "School Spirit Week starts next Monday!",
            "time": "2026-09-14T09:00:00Z",
            "attachments": [{"path": "http://img.url/photo.jpg"}]
        }
    ]

    new_c, upd_c = temp_db.upsert_feed_items(items)
    assert new_c == 2
    assert upd_c == 0

    # Test idempotency / update
    new_c2, upd_c2 = temp_db.upsert_feed_items(items)
    assert new_c2 == 0
    assert upd_c2 == 2

    undigested = temp_db.get_undigested_items()
    assert len(undigested["feed_items"]) == 2

    # Mark post_1 digested
    temp_db.mark_items_digested(["post_1"], [], [])
    undigested_after = temp_db.get_undigested_items()
    assert len(undigested_after["feed_items"]) == 1
    assert undigested_after["feed_items"][0]["id"] == "post_2"


def test_messages_and_events(temp_db):
    messages = [
        {
            "id": "msg_1",
            "sender_name": "Ms. Jenkins",
            "body": "Leo did a wonderful job on his math worksheet today!",
            "time": "2026-09-14T14:00:00Z"
        }
    ]
    events = [
        {
            "id": "event_1",
            "title": "Pumpkin Patch Field Trip",
            "description": "Bus leaves at 9:00 AM. Bring packed lunch.",
            "start_time": "2026-10-15T09:00:00Z"
        }
    ]

    temp_db.upsert_messages(messages)
    temp_db.upsert_events(events)

    undigested = temp_db.get_undigested_items()
    assert len(undigested["messages"]) == 1
    assert len(undigested["events"]) == 1

    temp_db.mark_items_digested([], ["msg_1"], ["event_1"])
    undigested_after = temp_db.get_undigested_items()
    assert len(undigested_after["messages"]) == 0
    assert len(undigested_after["events"]) == 0


def test_record_digest(temp_db):
    digest_id = temp_db.record_digest(
        content_html="<h1>Test</h1>",
        content_text="Test",
        recipient_email="parent@example.com",
        item_count=5,
        sent_status="sent"
    )
    assert digest_id > 0
