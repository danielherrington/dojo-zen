"""Read-only school email sync (DAN-395): Lehrman communicates by email only.

Reads Daniel's Gmail with the ``gmail.readonly`` scope, limited by a search query to the school's
senders, and keeps only dated items (date, short title, type) per message. Message bodies, senders'
names and attachments are never stored, returned or logged. Bodies are sent to Gemini only to
extract those items; without GEMINI_API_KEY a rule-based extractor is used.

Config (Secret Manager on Cloud Run): GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
(made once by ``scripts/gmail_consent.py``). LEHRMAN_GMAIL_QUERY overrides the default query.
"""

import base64
import html
import json
import logging
import os
import re
from datetime import date as date_cls
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

SOURCE = "lehrman"
DEFAULT_QUERY = "from:(lehrmanschool.org OR lehrmanschoolorg) newer_than:30d"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
ITEM_TYPES = {"day_off", "early_dismissal", "deadline", "event"}
MAX_TITLE = 100
MAX_ITEMS_PER_MESSAGE = 8


def gmail_query() -> str:
    return os.getenv("LEHRMAN_GMAIL_QUERY", "").strip() or DEFAULT_QUERY


def gmail_configured() -> bool:
    return all(os.getenv(n, "").strip() for n in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"))


# --- Gmail (REST, read-only) ---

def access_token(client: httpx.Client) -> str:
    resp = client.post(TOKEN_URL, data={
        "client_id": os.environ["GMAIL_CLIENT_ID"],
        "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
        "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def body_text(payload: Dict[str, Any]) -> str:
    """Plain text of a Gmail message payload (text/plain preferred, else text/html stripped)."""
    plain, rich = [], []

    def walk(part: Dict[str, Any]) -> None:
        mime, data = part.get("mimeType", ""), (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data))
        elif data and mime == "text/html":
            rich.append(_html_to_text(_b64(data)))
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    text = "\n".join(plain) if plain else "\n".join(rich)
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parse_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    headers = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}
    received = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000, tz=timezone.utc)
    return {"id": msg["id"], "subject": headers.get("subject", ""), "received": received,
            "text": body_text(msg.get("payload") or {})}


def fetch_messages(query: str, skip: Iterable[str] = (), limit: int = 40) -> List[Dict[str, Any]]:
    """Messages matching ``query`` not already in ``skip`` (newest first)."""
    skip = set(skip)
    with httpx.Client(timeout=30) as client:
        headers = {"Authorization": f"Bearer {access_token(client)}"}
        listing = client.get(f"{GMAIL_API}/messages", headers=headers, params={"q": query, "maxResults": limit})
        listing.raise_for_status()
        out = []
        for ref in listing.json().get("messages", []):
            if ref["id"] in skip:
                continue
            resp = client.get(f"{GMAIL_API}/messages/{ref['id']}", headers=headers, params={"format": "full"})
            resp.raise_for_status()
            out.append(parse_message(resp.json()))
        return out


# --- Extraction ---

PROMPT = """You read one email from a school to parents. List the dated things a parent needs to know:
days off / no school, early dismissals, deadlines (forms, payments, sign-ups, things to bring) and events.
Rules: only items with a specific calendar date; resolve relative dates using the email's received date;
title is at most 12 words, plain text, and names no child, parent or staff member; skip fundraising appeals
and past dates. Reply with JSON only: {"items": [{"date": "YYYY-MM-DD", "title": "...",
"type": "day_off|early_dismissal|deadline|event"}]}. Use {"items": []} if there are none."""


def _clean_items(raw: Any, received: date_cls) -> List[Dict[str, str]]:
    out = []
    for it in (raw or [])[:MAX_ITEMS_PER_MESSAGE]:
        if not isinstance(it, dict):
            continue
        try:
            day = date_cls.fromisoformat(str(it.get("date", ""))[:10])
        except ValueError:
            continue
        title = re.sub(r"\s+", " ", str(it.get("title", ""))).strip()[:MAX_TITLE]
        kind = it.get("type") if it.get("type") in ITEM_TYPES else "event"
        if title and (day - received).days >= -1:
            out.append({"date": day.isoformat(), "title": title, "type": kind})
    return out


def extract_with_gemini(msg: Dict[str, Any], api_key: str) -> List[Dict[str, str]]:
    received = msg["received"].date()
    text = f"Received: {received.isoformat()}\nSubject: {msg['subject']}\n\n{msg['text'][:15000]}"
    resp = httpx.post(GEMINI_URL, headers={"x-goog-api-key": api_key}, timeout=45, json={
        "contents": [{"parts": [{"text": PROMPT}, {"text": text}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    })
    resp.raise_for_status()
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    return _clean_items(json.loads(raw).get("items"), received)


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)


def extract_with_rules(msg: Dict[str, Any]) -> List[Dict[str, str]]:
    """Fallback without Gemini: sentences that carry an explicit 'Month Day' date."""
    from dojo.logistics import classify

    received = msg["received"].date()
    items = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", f"{msg['subject']}\n{msg['text']}"):
        m = _DATE.search(sentence)
        if not m:
            continue
        month, day = _MONTHS[m.group(1)[:3].lower()], int(m.group(2))
        year = received.year + (1 if month < received.month - 6 else 0)
        try:
            when = date_cls(year, month, day)
        except ValueError:
            continue
        title = sentence.strip(" -*•\t")
        items.append({"date": when.isoformat(), "title": title, "type": classify(title)})
    return _clean_items(items, received)


def extract_items(msg: Dict[str, Any], api_key: Optional[str] = None) -> List[Dict[str, str]]:
    if api_key:
        try:
            return extract_with_gemini(msg, api_key)
        except Exception as e:
            logger.warning(f"Gemini extraction failed ({type(e).__name__}); using rules.")
    return extract_with_rules(msg)


# --- Sync ---

def sync(
    db: Any,
    fetch: Callable[..., List[Dict[str, Any]]] = fetch_messages,
    extract: Callable[[Dict[str, Any]], List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Fetch new school emails and store their dated items. Returns counts only."""
    if extract is None:
        from dojo.config import settings
        key = settings.gemini_api_key

        def extract(m):
            return extract_items(m, key)

    seen = db.get_school_message_ids(SOURCE)
    messages = fetch(gmail_query(), skip=seen)
    rows = []
    for msg in messages:
        items = extract(msg)
        received = msg["received"].isoformat()
        for n, it in enumerate(items):
            rows.append({"id": f"{msg['id']}#{n}", "source": SOURCE, "message_id": msg["id"],
                         "date": it["date"], "title": it["title"], "type": it["type"], "received_at": received})
        # Marker so a message with nothing dated isn't sent to Gemini again.
        rows.append({"id": f"{msg['id']}#seen", "source": SOURCE, "message_id": msg["id"],
                     "date": None, "title": "", "type": "seen", "received_at": received})
    db.upsert_school_items(rows)
    found = sum(1 for r in rows if r["type"] != "seen")
    logger.info("School email sync: %d new messages, %d dated items.", len(messages), found)
    return {"source": SOURCE, "new_messages": len(messages), "items": found}
