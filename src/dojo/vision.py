"""Vision & Multimodal Analysis module for ClassDojo attachments.

Extracts text, dates, schedules, and action items from school flyers, homework packets,
and photo announcements using Google Gemini multimodal models (gemini-3.6-flash).
"""

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from dojo.config import settings

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"

VISION_SYSTEM_PROMPT = """You are an expert OCR and school announcement parser assisting parents.
Analyze this school photo or document image.
1. Determine if the image contains readable text (flyer, schedule, poster, worksheet, permission slip, letter, handwritten note).
2. If text exists: perform accurate OCR transcription of all readable text.
3. Extract any specific dates, days, times, and locations (e.g., "September 16th @ 8:30 AM", "Tomorrow", "Next Wednesday").
4. Extract specific parent action items (e.g. "RSVP via link", "Wear sneakers", "Bring a poster board", "Sign permission slip").
5. Provide a brief 1-2 sentence high-signal summary.

Respond ONLY with a valid JSON object matching this exact schema:
{
  "has_text": true,
  "full_text": "Complete OCR transcript here",
  "dates": [
    {
      "title": "Event or deadline name",
      "date_str": "e.g. Sep 16",
      "time": "e.g. 8:30 AM",
      "location": "e.g. Classroom 6-017"
    }
  ],
  "action_items": [
    {
      "summary": "Clear parent task",
      "urgency": "normal"
    }
  ],
  "summary": "Brief 1-2 sentence overview of the image content"
}

If the image is just a casual photo of students/classroom with no meaningful instructional text, set has_text to false, full_text to "", dates to [], action_items to [], and summary to a brief visual description (e.g. "Students working at math stations")."""


def download_image(url: str, cookies: Optional[Dict[str, str]] = None, timeout: float = 20.0) -> Optional[bytes]:
    """Download image attachment bytes from a URL."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
        }
        resp = httpx.get(url, headers=headers, cookies=cookies, timeout=timeout, follow_redirects=True)
        if resp.status_code == 200:
            return resp.content
        logger.warning(f"Failed to download image {url[:60]}: status {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"Error downloading image attachment: {e}")
        return None


def analyze_image_bytes(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Analyze an image using Gemini 3.6 Flash multimodal vision.

    Returns structured OCR dict with full_text, dates, action_items, and summary.
    """
    key = settings.gemini_api_key if api_key is None else api_key
    if not key:
        logger.debug("No GEMINI_API_KEY configured; skipping vision analysis.")
        return {
            "has_text": False,
            "full_text": "",
            "dates": [],
            "action_items": [],
            "summary": "OCR skipped (no API key)"
        }

    try:
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        url = f"{GEMINI_API_URL}?key={key}"

        payload = {
            "contents": [{
                "parts": [
                    {"text": VISION_SYSTEM_PROMPT},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": b64_data
                        }
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json"
            }
        }

        resp = httpx.post(url, json=payload, timeout=30.0)
        if resp.status_code != 200:
            logger.warning(f"Gemini Vision API error ({resp.status_code}): {resp.text[:200]}")
            return {
                "has_text": False,
                "full_text": "",
                "dates": [],
                "action_items": [],
                "summary": f"OCR failed ({resp.status_code})"
            }

        result = resp.json()
        raw_text = result["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Clean JSON if wrapped in markdown code blocks
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text)

        parsed = json.loads(raw_text)
        return {
            "has_text": bool(parsed.get("has_text", False)),
            "full_text": str(parsed.get("full_text", "")).strip(),
            "dates": list(parsed.get("dates", [])),
            "action_items": list(parsed.get("action_items", [])),
            "summary": str(parsed.get("summary", "")).strip()
        }

    except Exception as e:
        logger.error(f"Vision analysis exception: {e}")
        return {
            "has_text": False,
            "full_text": "",
            "dates": [],
            "action_items": [],
            "summary": f"OCR error: {e}"
        }


def analyze_feed_attachments(
    item: Dict[str, Any],
    cookies: Optional[Dict[str, str]] = None,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Download and analyze all image attachments for a given feed post."""
    attachments = item.get("attachments") or item.get("contents", {}).get("attachments") or []
    if not attachments and item.get("attachments_json"):
        try:
            attachments = json.loads(item["attachments_json"])
        except Exception:
            attachments = []

    results = []
    for att in attachments:
        path = att.get("path")
        if not path:
            continue

        img_bytes = download_image(path, cookies=cookies)
        if not img_bytes:
            continue

        # Detect mime type from filename or header
        fn = att.get("metadata", {}).get("filename", "")
        mime = "image/png" if fn.endswith(".png") else "image/jpeg"
        ocr_res = analyze_image_bytes(img_bytes, mime_type=mime, api_key=api_key)
        ocr_res["attachment_id"] = att.get("_id") or att.get("id")
        ocr_res["image_url"] = path
        results.append(ocr_res)

    return results
