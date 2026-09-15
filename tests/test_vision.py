"""Unit tests for multimodal image analysis, OCR, and downstream integrations."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from dojo.vision import analyze_image_bytes, analyze_feed_attachments
from dojo.db import DojoDatabase
from dojo.qa import DojoQA
from dojo.digest import DigestEngine


def test_analyze_image_bytes_no_key():
    """Ensure graceful fallback when no API key is set."""
    res = analyze_image_bytes(b"dummy_bytes", api_key="")
    assert res["has_text"] is False
    assert res["full_text"] == ""
    assert res["dates"] == []
    assert res["action_items"] == []
    assert "OCR skipped" in res["summary"]


def test_analyze_image_bytes_success():
    """Ensure Gemini response parsing works with markdown code blocks."""
    mock_payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": "```json\n" + json.dumps({
                        "has_text": True,
                        "full_text": "DONUTS WITH DUDES\nDate: Sept 16 at 8:30 AM\nRoom 6-017",
                        "dates": ["Sept 16 at 8:30 AM"],
                        "action_items": ["RSVP in room 6-017"],
                        "summary": "Donuts with Dudes event on Sept 16 at 8:30 AM"
                    }) + "\n```"
                }]
            }
        }]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.post", return_value=mock_resp):
        res = analyze_image_bytes(b"image_content", api_key="fake_key")
        assert res["has_text"] is True
        assert "DONUTS WITH DUDES" in res["full_text"]
        assert "Sept 16 at 8:30 AM" in res["dates"]
        assert "RSVP in room 6-017" in res["action_items"]
        assert "Donuts with Dudes" in res["summary"]


def test_analyze_image_bytes_error_handling():
    """Ensure HTTP errors return fallback without raising exceptions."""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch("httpx.post", return_value=mock_resp):
        res = analyze_image_bytes(b"image_content", api_key="fake_key")
        assert res["has_text"] is False
        assert "OCR failed" in res["summary"]


def test_analyze_feed_attachments_flow():
    """Test analyzing attachments on a feed item with downloaded bytes."""
    item = {
        "id": "post_123",
        "attachments": [
            {
                "id": "att_1",
                "path": "https://sphotos.classdojo.com/flyer.png",
                "metadata": {"filename": "flyer.png"}
            }
        ]
    }

    mock_download = MagicMock(return_value=b"fake_png_bytes")
    mock_ocr = {
        "has_text": True,
        "full_text": "PICTURE DAY TOMORROW",
        "dates": ["Tomorrow"],
        "action_items": ["Wear uniforms"],
        "summary": "Picture day notice"
    }

    with patch("dojo.vision.download_image", mock_download), \
         patch("dojo.vision.analyze_image_bytes", return_value=mock_ocr):
        results = analyze_feed_attachments(item, api_key="fake_key")
        assert len(results) == 1
        assert results[0]["has_text"] is True
        assert results[0]["attachment_id"] == "att_1"
        assert results[0]["image_url"] == "https://sphotos.classdojo.com/flyer.png"
        assert "PICTURE DAY" in results[0]["full_text"]


def test_database_ocr_persistence():
    """Test saving and retrieving OCR data in DojoDatabase."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DojoDatabase(Path(tmpdir) / "test.db")
        db.upsert_feed_items([{
            "id": "item_flyer",
            "senderName": "Principal",
            "content_text": "Attached is this week's flyer.",
            "item_timestamp": "2026-09-15T09:00:00Z"
        }])

        ocr_sample = [{
            "has_text": True,
            "full_text": "SCHOLASTIC BOOK FAIR\nSept 20-24",
            "dates": ["Sept 20-24"],
            "action_items": ["Send eWallet money"],
            "summary": "Scholastic Book Fair flyer"
        }]

        db.update_item_ocr("item_flyer", ocr_sample)
        retrieved = db.get_feed_item("item_flyer")
        assert retrieved is not None
        assert retrieved["ocr_json"] is not None
        parsed = json.loads(retrieved["ocr_json"])
        assert parsed[0]["summary"] == "Scholastic Book Fair flyer"


def test_qa_indexes_ocr_transcript():
    """Test that DojoQA indexes and searches OCR image transcript content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DojoDatabase(Path(tmpdir) / "test.db")
        db.upsert_feed_items([{
            "id": "item_donuts",
            "senderName": "Ms. Garcia",
            "content_text": "See flyer for details!",
            "item_timestamp": "2026-09-15T09:00:00Z"
        }])

        ocr_sample = [{
            "has_text": True,
            "full_text": "DONUTS WITH DUDES\nJoin us in Room 6-017 at 8:30 AM",
            "dates": ["September 16th @ 8:30 AM"],
            "action_items": ["RSVP via parent chat"],
            "summary": "Donuts with Dudes breakfast"
        }]
        db.update_item_ocr("item_donuts", ocr_sample)

        qa = DojoQA(db)
        ans = qa.answer_question("When is Donuts with Dudes?")
        assert ans.confidence in ("high", "medium")
        assert len(ans.sources) >= 1
        assert any("donuts with dudes" in s.snippet.lower() for s in ans.sources)


def test_digest_ocr_action_item_integration():
    """Test that DigestEngine incorporates OCR action items into daily briefing."""
    digest = DigestEngine()

    action_items = []
    upcoming_dates = []
    ocr_data = {
        "has_text": True,
        "full_text": "PIZZA FRIDAY - Return envelope by Thursday",
        "dates": [{"title": "Pizza Friday", "date_str": "Friday", "time": "12:00 PM"}],
        "action_items": ["Return money envelope by Thursday"],
        "summary": "Pizza Friday ordering details"
    }

    digest._integrate_ocr_into_briefing(
        ocr=ocr_data,
        author="Ms. Alonso",
        posted_str="Today at 10:00 AM",
        is_stale_post=False,
        action_items=action_items,
        upcoming_dates=upcoming_dates
    )

    assert len(action_items) == 1
    assert "[Flyer Note]" in action_items[0].summary
    assert "Return money envelope by Thursday" in action_items[0].summary
    assert len(upcoming_dates) == 1
    assert "Pizza Friday" in upcoming_dates[0].title
