"""FastAPI web server for the Dojo Zen Web Portal & Q&A Assistant."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Form, Header, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dojo.config import settings
from dojo.db import get_database
from dojo.client import DojoClient
from dojo.digest import DigestEngine, parse_and_format_timestamp
from dojo.qa import DojoQA, QAResult

logger = logging.getLogger(__name__)

app = FastAPI(title="Dojo Zen Web Portal", description="Classroom Assistant for Daniel & Family")

db = get_database()
qa_engine = DojoQA(db, gemini_api_key=settings.gemini_api_key)
digest_engine = DigestEngine(gemini_api_key=settings.gemini_api_key)

WEB_DIR = Path(__file__).parent / "web"


def _verify_cron_secret(authorization: Optional[str] = None, secret: Optional[str] = None) -> None:
    """Verify bearer token or query secret matches CRON_SECRET."""
    token = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
    if not token and secret:
        token = secret

    expected = settings.cron_secret
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized: invalid cron secret token.")


class AskRequest(BaseModel):
    query: str


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Web application UI not found")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.post("/api/ask", response_model=QAResult)
async def ask_question(req: AskRequest):
    """Answer natural language queries using grounded classroom data."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    return qa_engine.answer_question(req.query)


@app.get("/api/briefing")
async def get_briefing():
    """Fetch the latest live daily briefing without waiting for email."""
    feed_items = db.get_all_feed_items(limit=50)
    messages = db.get_all_messages(limit=20)
    events = db.get_all_events()
    children = db.get_all_children()

    briefing = digest_engine.synthesize(feed_items, messages, events, children)

    return {
        "briefing": {
            "generated_at": briefing.generated_at,
            "action_items": [a.model_dump() for a in briefing.active_action_items],
            "upcoming_dates": [d.model_dump() for d in briefing.upcoming_dates],
            "teacher_notes": [m.model_dump() for m in briefing.teacher_notes],
            "classroom_highlights": [h.model_dump() for h in briefing.classroom_highlights[:5]],
            "raw_item_count": briefing.raw_item_count,
        }
    }


@app.get("/api/feed")
async def get_feed(limit: int = 40):
    """Retrieve classroom feed with formatted local timestamps, authors, attachments, and OCR transcripts."""
    items = []
    rows = db.get_all_feed_items(limit=limit)
    for r in rows:
        text = (r.get("content_text") or r.get("header") or "").strip()
        if not text:
            continue
        posted_str, _ = parse_and_format_timestamp(r.get("item_timestamp"))

        # Parse attachments
        attachments = []
        raw_att = r.get("attachments_json")
        if raw_att:
            try:
                attachments = json.loads(raw_att) if isinstance(raw_att, str) else raw_att
            except Exception:
                attachments = []

        # Parse OCR data
        ocr_data = None
        raw_ocr = r.get("ocr_json") or r.get("ocr_data")
        if raw_ocr:
            try:
                ocr_data = json.loads(raw_ocr) if isinstance(raw_ocr, str) else raw_ocr
            except Exception:
                ocr_data = None

        items.append({
            "id": r["id"],
            "author": r.get("author_name") or "School",
            "header": r.get("header"),
            "text": text,
            "posted_at_str": posted_str,
            "raw_time": r.get("item_timestamp"),
            "classdojo_url": f"https://home.classdojo.com/#/story/{r['id']}",
            "attachments": attachments,
            "ocr_data": ocr_data,
        })
    return {"items": items}


@app.get("/api/feed/{item_id}")
async def get_single_feed_item(item_id: str):
    """Retrieve a single post by ID for deep linking."""
    item = db.get_feed_item(item_id) if hasattr(db, "get_feed_item") else None
    if not item:
        raise HTTPException(status_code=404, detail="Feed item not found")
    posted_str, _ = parse_and_format_timestamp(item.get("item_timestamp"))

    attachments = []
    raw_att = item.get("attachments_json")
    if raw_att:
        try:
            attachments = json.loads(raw_att) if isinstance(raw_att, str) else raw_att
        except Exception:
            attachments = []

    ocr_data = None
    raw_ocr = item.get("ocr_json") or item.get("ocr_data")
    if raw_ocr:
        try:
            ocr_data = json.loads(raw_ocr) if isinstance(raw_ocr, str) else raw_ocr
        except Exception:
            ocr_data = None

    return {
        "id": item["id"],
        "author": item.get("author_name") or "School",
        "header": item.get("header"),
        "text": (item.get("content_text") or item.get("header") or "").strip(),
        "posted_at_str": posted_str,
        "raw_time": item.get("item_timestamp"),
        "classdojo_url": f"https://home.classdojo.com/#/story/{item['id']}",
        "attachments": attachments,
        "ocr_data": ocr_data,
    }


@app.post("/api/feed/{item_id}/analyze")
async def analyze_single_feed_item(item_id: str):
    """Analyze image attachments for a post using Gemini Multimodal OCR."""
    from dojo.vision import analyze_feed_attachments
    item = db.get_feed_item(item_id) if hasattr(db, "get_feed_item") else None
    if not item:
        raise HTTPException(status_code=404, detail="Feed item not found")

    client = DojoClient(session_file=settings.dojo_session_file)
    cookies = {c.name: c.value for c in client.client.cookies.jar}

    ocr_results = analyze_feed_attachments(item, cookies=cookies)
    if ocr_results:
        db.update_item_ocr(item_id, ocr_results)
    return {"item_id": item_id, "ocr_results": ocr_results}


@app.post("/api/sync")
async def sync_now():
    """Trigger an on-demand sync from ClassDojo."""
    client = DojoClient(session_file=settings.dojo_session_file)
    if not client.is_authenticated():
        raise HTTPException(status_code=401, detail="No active ClassDojo session. Please refresh your session.")

    try:
        feed_items = client.get_story_feed(limit=50)
        new_feed, upd_feed = db.upsert_feed_items(feed_items)

        messages = client.get_messages()
        new_msg, upd_msg = db.upsert_messages(messages)

        events = client.get_events()
        new_ev, upd_ev = db.upsert_events(events)
        client.close()

        total_new = new_feed + new_msg + new_ev
        return {
            "status": "ok",
            "new_items": total_new,
            "updated_items": upd_feed + upd_msg + upd_ev
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status")
async def get_status():
    """Health and database statistics."""
    stats = db.get_stats()
    return {
        "status": "online",
        "stats": stats,
        "database_backend": "firestore" if settings.use_firestore else "sqlite",
    }


# --- Twilio SMS / WhatsApp / RCS Inbound Webhook ---

@app.post("/api/sms/webhook")
async def twilio_sms_webhook(
    From: str = Form(""),
    Body: str = Form(""),
    To: str = Form(""),
    AccountSid: Optional[str] = Form(None),
    MessageSid: Optional[str] = Form(None),
):
    """Inbound webhook called by Twilio when an SMS, WhatsApp, or RCS text is received."""
    from dojo.sms import handle_incoming_sms

    logger.info(f"Incoming text from {From} (To: {To}): {Body[:60]}")
    twiml = handle_incoming_sms(
        from_number=From,
        query_text=Body,
        qa_engine=qa_engine,
        custom_settings=settings
    )
    return Response(content=twiml, media_type="application/xml")


# --- Scheduled Cloud Cron Webhooks ---

@app.post("/api/cron/check-alerts")
async def cron_check_alerts(
    authorization: Optional[str] = Header(None),
    secret: Optional[str] = Query(None)
):
    """Webhook triggered by Cloud Scheduler (e.g. every 10 min) to scan for urgent alerts."""
    _verify_cron_secret(authorization, secret)

    try:
        from dojo.monitor import MessageMonitor
        client = DojoClient(session_file=settings.dojo_session_file)
        if not client.is_authenticated():
            logger.warning("ClassDojo session is not authenticated; skipping API sync to prevent 2FA triggers.")
            return {"status": "skipped", "reason": "ClassDojo session not authenticated"}

        monitor = MessageMonitor(settings=settings, db=db, client=client)
        alerts = monitor.check_once(send_email=True)
        if isinstance(alerts, list):
            serialized_alerts = [a.model_dump() if hasattr(a, "model_dump") else str(a) for a in alerts]
            urgent_count = len(alerts)
        else:
            serialized_alerts = []
            urgent_count = 0
        return {
            "status": "ok",
            "urgent_alerts_sent": urgent_count,
            "alerts": serialized_alerts
        }
    except Exception as e:
        logger.error(f"Cron check-alerts error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cron/daily-digest")
async def cron_daily_digest(
    authorization: Optional[str] = Header(None),
    secret: Optional[str] = Query(None),
    force: bool = Query(False)
):
    """Webhook triggered by Cloud Scheduler (e.g. 7:00 AM daily) to send morning recap."""
    _verify_cron_secret(authorization, secret)

    try:
        # 1. Sync latest from ClassDojo if session is valid
        client = DojoClient(session_file=settings.dojo_session_file)
        if client.is_authenticated():
            try:
                feed = client.get_story_feed(limit=50)
                db.upsert_feed_items(feed)
                db.upsert_messages(client.get_messages())
                db.upsert_events(client.get_events())
                client.close()
            except Exception as e:
                logger.warning(f"ClassDojo sync failed: {e}")
        else:
            logger.warning("ClassDojo session not authenticated; compiling briefing from cached items.")

        # 2. Compile and dispatch daily briefing
        from dojo.mailer import dispatch_daily_briefing
        result = dispatch_daily_briefing(db=db, force=force)
        return {"status": "ok", "dispatch": result}
    except Exception as e:
        logger.error(f"Cron daily-digest error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Entry point to launch the server with uvicorn."""
    import uvicorn
    print(f"\n🎒 Dojo Zen Web Portal launching on http://{host}:{port}")
    print(f"👉 Local access: http://localhost:{port}")
    print(f"👉 Backend: {'Firestore' if settings.use_firestore else 'SQLite'}\n")
    uvicorn.run("dojo.server:app", host=host, port=port, reload=False)
