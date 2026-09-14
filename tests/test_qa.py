"""Unit tests for the DojoQA search and question answering engine."""

import tempfile
from pathlib import Path
import pytest

from dojo.db import DojoDatabase
from dojo.qa import DojoQA


@pytest.fixture
def qa_system():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DojoDatabase(Path(tmpdir) / "qa_test.db")
        # Populate sample feed items
        db.upsert_feed_items([
            {
                "id": "item_hw",
                "senderName": "Ms. Alonso",
                "headerSubtext": "Ms. Alonso's Class",
                "content_text": "Good afternoon parents, there is no formal math homework tonight—just some light review!",
                "item_timestamp": "2026-09-14T13:00:00Z"
            },
            {
                "id": "item_headphones",
                "senderName": "Ms. Alonso",
                "headerSubtext": "Ms. Alonso's Class",
                "content_text": "Just a quick reminder to please put your child's headphones in their bookbag tonight.",
                "item_timestamp": "2026-09-14T11:00:00Z"
            },
            {
                "id": "item_test",
                "senderName": "Ms. Alonso",
                "headerSubtext": "Ms. Alonso's Class",
                "content_text": "Students will be taking their FAST reading test this Friday.",
                "item_timestamp": "2026-09-14T13:06:28Z"
            },
            {
                "id": "item_apples",
                "senderName": "Ms. Alonso",
                "headerSubtext": "Ms. Alonso's Class",
                "content_text": "Olivia's mom is bringing red apples, Isabella's mom is bringing yellow apples for our science unit.",
                "item_timestamp": "2026-09-01T10:00:00Z"
            }
        ])

        # Sample event
        db.upsert_events([
            {
                "id": "ev_fall_pic",
                "title": "Fall Picture Day",
                "description": "Wear your favorite outfit!",
                "start_time": "2026-09-25"
            }
        ])

        yield DojoQA(db)


def test_answer_homework_query(qa_system):
    result = qa_system.answer_question("Is there homework tonight?")
    assert result.confidence in ("high", "medium")
    assert "homework" in result.answer.lower()
    assert len(result.sources) >= 1
    assert "Ms. Alonso" in result.sources[0].author


def test_answer_supplies_query(qa_system):
    result = qa_system.answer_question("What does he need to bring in his bookbag?")
    assert len(result.sources) >= 1
    assert any("headphones" in s.snippet.lower() for s in result.sources)


def test_answer_testing_query(qa_system):
    result = qa_system.answer_question("When is the FAST test?")
    assert len(result.sources) >= 1
    assert any("fast reading test" in s.snippet.lower() for s in result.sources)


def test_unrelated_query(qa_system):
    result = qa_system.answer_question("What is the capital of France?")
    assert "didn't find any mentions" in result.answer.lower()
