"""SMS & WhatsApp Assistant Handler for Twilio/RCS integration."""

import html
import re
from typing import Optional, Set
from dojo.config import Settings, settings
from dojo.qa import DojoQA


def normalize_phone_number(raw_number: str) -> str:
    """Normalize incoming phone numbers by stripping whatsapp: prefix, non-digits, and ensuring standard +E.164 format."""
    if not raw_number:
        return ""
    clean = raw_number.strip()
    if clean.lower().startswith("whatsapp:"):
        clean = clean[9:].strip()
    # Strip common formatting like spaces, dashes, parentheses
    digits = re.sub(r"\D", "", clean)
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    elif clean.startswith("+"):
        return f"+{digits}"
    return digits


def is_authorized_sender(raw_number: str, custom_settings: Optional[Settings] = None) -> bool:
    """Verify whether sender is authorized via family_phone_numbers whitelist."""
    s = custom_settings or settings
    whitelist_raw = s.family_phone_numbers.strip()
    if not whitelist_raw:
        # If no whitelist is configured, allow for development / testing
        return True

    allowed_numbers: Set[str] = {
        normalize_phone_number(num) for num in whitelist_raw.split(",") if num.strip()
    }
    sender_clean = normalize_phone_number(raw_number)
    return sender_clean in allowed_numbers


def format_sms_response(answer: str, max_length: int = 480) -> str:
    """Clean markdown artifacts and ensure answers are concise for SMS/RCS delivery."""
    if not answer:
        return "I couldn't find any information on that in ClassDojo."

    # Remove bold markdown ** or *
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", answer)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    # Remove citation brackets like [1], [Source: ...]
    text = re.sub(r"\(Source:[^)]+\)", "", text)
    # Simplify bullet points
    text = text.replace("• ", "- ")
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) > max_length:
        text = text[: max_length - 3].rsplit(" ", 1)[0] + "..."

    return text


def generate_twiml_response(message_body: str) -> str:
    """Generate standard TwiML XML to reply to Twilio SMS, WhatsApp, or RCS."""
    escaped_body = html.escape(message_body)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Response>\n"
        f"    <Message>{escaped_body}</Message>\n"
        f"</Response>"
    )


def handle_incoming_sms(
    from_number: str,
    query_text: str,
    qa_engine: DojoQA,
    custom_settings: Optional[Settings] = None
) -> str:
    """Process an inbound text and return a TwiML XML string response."""
    if not is_authorized_sender(from_number, custom_settings):
        return generate_twiml_response(
            "Sorry, this ClassDojo assistant is private and only available to authorized family members."
        )

    clean_query = query_text.strip()
    if not clean_query:
        return generate_twiml_response(
            "Hi! Ask me anything about school (e.g. 'Is there math homework tonight?' or 'When is Izzy's project due?')."
        )

    try:
        qa_result = qa_engine.answer_question(clean_query)
        sms_text = format_sms_response(qa_result.answer)
    except Exception as e:
        sms_text = f"Error checking ClassDojo: {str(e)[:100]}"

    return generate_twiml_response(sms_text)
