"""Unit tests for the SMS & WhatsApp / RCS assistant webhook."""

import pytest
from starlette.testclient import TestClient

from dojo.config import Settings
from dojo.sms import (
    format_sms_response,
    generate_twiml_response,
    handle_incoming_sms,
    is_authorized_sender,
    normalize_phone_number,
)
from dojo.server import app


def test_normalize_phone_number():
    assert normalize_phone_number("+13015550123") == "+13015550123"
    assert normalize_phone_number("whatsapp:+13015550123") == "+13015550123"
    assert normalize_phone_number("  whatsapp:+15559998888  ") == "+15559998888"
    assert normalize_phone_number("2409947266") == "+12409947266"
    assert normalize_phone_number("(240) 994-7266") == "+12409947266"
    assert normalize_phone_number("whatsapp:240-994-7266") == "+12409947266"
    assert normalize_phone_number("") == ""


def test_is_authorized_sender():
    test_settings = Settings(
        family_phone_numbers="+13015550100,+13015550200"
    )

    # Standard SMS
    assert is_authorized_sender("+13015550100", test_settings) is True
    assert is_authorized_sender("+13015550200", test_settings) is True
    assert is_authorized_sender("+19998887777", test_settings) is False

    # WhatsApp prefixed
    assert is_authorized_sender("whatsapp:+13015550100", test_settings) is True
    assert is_authorized_sender("whatsapp:+19998887777", test_settings) is False

    # Empty whitelist allows all (dev mode)
    open_settings = Settings(family_phone_numbers="")
    assert is_authorized_sender("+19998887777", open_settings) is True


def test_format_sms_response():
    markdown_text = "Here is the note: **Project due tomorrow!** Please bring *supplies*.\n\n• Point 1\n• Point 2\n(Source: Ms. Alonso · Sep 14)"
    clean = format_sms_response(markdown_text)
    assert "**" not in clean
    assert "*" not in clean
    assert "(Source:" not in clean
    assert "- Point 1" in clean
    assert "Project due tomorrow!" in clean


def test_generate_twiml_response():
    twiml = generate_twiml_response("Math homework is pages 1 & 2.")
    assert '<?xml version="1.0" encoding="UTF-8"?>' in twiml
    assert "<Response>" in twiml
    assert "<Message>Math homework is pages 1 &amp; 2.</Message>" in twiml


def test_sms_webhook_integration(monkeypatch):
    from dojo.config import settings
    monkeypatch.setattr(settings, "family_phone_numbers", "+13015550123,+12409947266")
    client = TestClient(app)

    # 1. Successful query via authorized SMS number
    response = client.post(
        "/api/sms/webhook",
        data={
            "From": "+13015550123",
            "Body": "When is Izzy's project due?",
            "To": "+18005550199"
        }
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    content = response.text
    assert "<Response>" in content
    assert "<Message>" in content

    # 2. Empty query prompt from authorized number
    empty_resp = client.post(
        "/api/sms/webhook",
        data={
            "From": "+12409947266",
            "Body": "",
            "To": "+18005550199"
        }
    )
    assert empty_resp.status_code == 200
    assert "Ask me anything about school" in empty_resp.text

    # 3. Unauthorized number receives polite rejection
    unauth_resp = client.post(
        "/api/sms/webhook",
        data={
            "From": "+19998887777",
            "Body": "What is the homework?",
            "To": "+18005550199"
        }
    )
    assert unauth_resp.status_code == 200
    assert "private and only available to authorized family members" in unauth_resp.text
