import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Form, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.routing import Route
from mcp.server.transport_security import TransportSecuritySettings

from dojo.config import settings
from dojo.auth import (
    SESSION_COOKIE,
    AccessGuardMiddleware,
    cron_token_is_valid,
    is_cloud_run,
    safe_next,
    token_is_valid,
    twilio_request_is_valid,
)
from dojo.analytics import (
    EVENT_CLICK,
    EVENT_OPEN,
    FUNNELS,
    PIXEL_GIF,
    UsageMiddleware,
    client_category,
    funnel_click_step,
    is_email_id,
    summarize,
)
from dojo.usage_v1 import emit
from dojo.db import get_database
from dojo.client import DojoClient, sync_roster
from dojo.digest import DigestEngine, parse_and_format_timestamp
from dojo.qa import DojoQA, QAResult
from dojo.mcp_server import mcp as mcp_server, set_database

logger = logging.getLogger(__name__)

db = get_database()
qa_engine = DojoQA(db, gemini_api_key=settings.gemini_api_key)
digest_engine = DigestEngine(gemini_api_key=settings.gemini_api_key)

# Inject database so MCP server uses the exact same database backend (Firestore in prod, SQLite locally)
set_database(db)

# Create streamable HTTP app for MCP server
mcp_subapp = mcp_server.streamable_http_app(
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager that starts the MCP Streamable HTTP session manager."""
    async with mcp_server._lowlevel_server._session_manager.run():
        yield


app = FastAPI(
    title="Dojo Zen Web Portal",
    description="Classroom Assistant for Daniel & Family",
    lifespan=lifespan,
)

# Route /mcp directly to avoid redirect and mount sub-app for /mcp/*
if mcp_subapp.routes:
    app.routes.append(Route("/mcp", endpoint=mcp_subapp.routes[0].endpoint))
app.mount("/mcp", mcp_subapp)

# Token gate for the portal, /api/* and /mcp (no-op unless REQUIRE_AUTH=true). DAN-166.
app.add_middleware(AccessGuardMiddleware)


def _record_usage(day: str, channel: str) -> None:
    if hasattr(db, "increment_usage"):
        db.increment_usage(day, channel)


# Per-channel daily usage counters (DAN-200). Outermost, so requests rejected by the token gate aren't counted.
app.add_middleware(UsageMiddleware, recorder=_record_usage, enabled=lambda: settings.usage_analytics)

WEB_DIR = Path(__file__).parent / "web"


def _verify_cron_secret(authorization: Optional[str] = None, secret: Optional[str] = None) -> None:
    """Verify bearer token or query secret matches CRON_SECRET (fails closed on Cloud Run if unset)."""
    token = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
    if not token and secret:
        token = secret

    if not cron_token_is_valid(token, settings):
        raise HTTPException(status_code=401, detail="Unauthorized: invalid cron secret token.")


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DojoZen Sign In</title>
<style>
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;color:#0f172a;
       display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}}
  form{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:24px;width:100%;max-width:360px}}
  h1{{font-size:20px;margin:0 0 8px}} p{{color:#475569;font-size:14px;margin:0 0 16px}}
  input{{width:100%;box-sizing:border-box;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font-size:16px}}
  button{{margin-top:12px;width:100%;padding:10px;border:0;border-radius:8px;background:#4f46e5;color:#fff;font-size:16px}}
  .err{{color:#dc2626}}
</style></head><body>
<form method="post" action="/login">
  <h1>🎒 DojoZen</h1>
  <p>This family portal is private. Enter your access code.</p>
  {error}
  <input type="password" name="token" autocomplete="current-password" placeholder="Access code" required autofocus>
  <input type="hidden" name="next" value="{next}">
  <button type="submit">Sign in</button>
</form></body></html>"""


def _render_login(next_path: str, error: str = "") -> str:
    import html as _html
    err = f'<p class="err">{_html.escape(error)}</p>' if error else ""
    return LOGIN_PAGE.format(error=err, next=_html.escape(safe_next(next_path), quote=True))


@app.get("/login", response_class=HTMLResponse)
async def login_page(next: str = "/"):
    return HTMLResponse(_render_login(next))


@app.post("/login")
async def login_submit(request: Request, token: str = Form(""), next: str = Form("/")):
    if not token_is_valid(token, settings):
        return HTMLResponse(_render_login(next, "That access code didn't work."), status_code=401)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https" or is_cloud_run()
    response = RedirectResponse(safe_next(next), status_code=303)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=60 * 60 * 24 * 180, httponly=True, secure=secure, samesite="lax", path="/",
    )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


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
    messages = db.get_all_messages(limit=30)
    events = db.get_all_events()
    children = db.get_all_children()

    briefing = digest_engine.synthesize(
        feed_items,
        messages,
        events,
        children,
        daily_focus=True,
        respect_digested=False,
    )

    return {
        "briefing": {
            "generated_at": briefing.generated_at,
            "action_items": [a.model_dump() for a in briefing.active_action_items],
            "upcoming_dates": [d.model_dump() for d in briefing.active_upcoming_dates],
            "teacher_notes": [m.model_dump() for m in briefing.teacher_notes],
            "classroom_highlights": [h.model_dump() for h in briefing.classroom_highlights[:5]],
            "expired_action_items": [],
            "raw_item_count": briefing.raw_item_count,
            "filtered_bloat_count": briefing.filtered_bloat_count,
        }
    }


@app.get("/api/feed")
async def get_feed(limit: int = 40):
    """Retrieve classroom feed (including Class Updates & teacher messages) with formatted local timestamps, authors, attachments, and OCR transcripts."""
    items = []
    rows = db.get_all_feed_items(limit=limit)
    for r in rows:
        text = (r.get("content_text") or r.get("header") or "").strip()
        if not text:
            continue
        posted_str, _ = parse_and_format_timestamp(r.get("item_timestamp"))

        # Parse attachments
        attachments = []
        raw_att = r.get("attachments") or r.get("attachments_json")
        if raw_att:
            try:
                attachments = json.loads(raw_att) if isinstance(raw_att, str) else raw_att
            except Exception:
                attachments = []

        # Parse OCR data
        ocr_data = None
        raw_ocr = r.get("ocr_data") or r.get("ocr_json")
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

    # Include Class Updates & teacher messages in the unified feed
    msg_rows = db.get_all_messages(limit=limit) if hasattr(db, "get_all_messages") else []
    for m in msg_rows:
        body = (m.get("body") or "").strip()
        if not body:
            continue
        posted_str, _ = parse_and_format_timestamp(m.get("message_timestamp"))
        attachments = []
        raw_att = m.get("attachments")
        if raw_att:
            try:
                attachments = json.loads(raw_att) if isinstance(raw_att, str) else raw_att
            except Exception:
                attachments = []
        items.append({
            "id": m["id"],
            "author": m.get("sender_name") or "Teacher (Class Updates)",
            "header": "Class Update / Message",
            "text": body,
            "posted_at_str": posted_str,
            "raw_time": m.get("message_timestamp"),
            "classdojo_url": "https://home.classdojo.com/#/messages",
            "attachments": attachments,
            "ocr_data": None,
        })

    items.sort(key=lambda x: x.get("raw_time") or "", reverse=True)
    return {"items": items[:limit]}


@app.get("/api/feed/{item_id}")
async def get_single_feed_item(item_id: str):
    """Retrieve a single post or message by ID for deep linking."""
    item = db.get_feed_item(item_id) if hasattr(db, "get_feed_item") else None
    if not item:
        # Check if it's a message / Class Update
        if hasattr(db, "get_all_messages"):
            for m in db.get_all_messages():
                if str(m.get("id")) == str(item_id):
                    posted_str, _ = parse_and_format_timestamp(m.get("message_timestamp"))
                    return {
                        "id": str(m.get("id")),
                        "author": m.get("sender_name") or "Teacher",
                        "header": "Class Update / Message",
                        "text": (m.get("body") or "").strip(),
                        "posted_at_str": posted_str,
                        "raw_time": m.get("message_timestamp"),
                        "classdojo_url": "https://home.classdojo.com/#/messages",
                        "attachments": m.get("attachments") or [],
                        "ocr_data": None,
                    }
        raise HTTPException(status_code=404, detail="Feed item not found")
    posted_str, _ = parse_and_format_timestamp(item.get("item_timestamp"))

    attachments = []
    raw_att = item.get("attachments") or item.get("attachments_json")
    if raw_att:
        try:
            attachments = json.loads(raw_att) if isinstance(raw_att, str) else raw_att
        except Exception:
            attachments = []

    ocr_data = None
    raw_ocr = item.get("ocr_data") or item.get("ocr_json")
    if raw_ocr:
        try:
            ocr_data = json.loads(raw_ocr) if isinstance(raw_ocr, str) else raw_ocr
        except Exception:
            ocr_data = None

    item_id_str = item.get("id") or item_id
    return {
        "id": item_id_str,
        "author": item.get("author_name") or "School",
        "header": item.get("header"),
        "text": (item.get("content_text") or item.get("header") or "").strip(),
        "posted_at_str": posted_str,
        "raw_time": item.get("item_timestamp"),
        "classdojo_url": f"https://home.classdojo.com/#/story/{item_id_str}",
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
        sync_roster(client, db)
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


@app.get("/.well-known/mcp-catalog.json", include_in_schema=False)
async def mcp_catalog():
    """Tool-catalog metadata for this MCP server (DAN-251 / DAN-244). Public: describes, never returns data."""
    from dojo.logistics import catalog_entry
    return catalog_entry(db)


# --- Twilio SMS / WhatsApp / RCS Inbound Webhook ---

def _twilio_candidate_urls(request: Request) -> List[str]:
    """URLs Twilio may have signed: the public portal URL and the URL as seen through any proxy."""
    path_qs = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return [
        settings.app_base_url.rstrip("/") + path_qs,
        f"{proto}://{host}{path_qs}",
        str(request.url),
    ]


@app.post("/api/sms/webhook")
async def twilio_sms_webhook(
    request: Request,
    From: str = Form(""),
    Body: str = Form(""),
    To: str = Form(""),
    AccountSid: Optional[str] = Form(None),
    MessageSid: Optional[str] = Form(None),
):
    """Inbound webhook called by Twilio when an SMS, WhatsApp, or RCS text is received."""
    from dojo.sms import handle_incoming_sms

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signed = twilio_request_is_valid(
        settings.twilio_auth_token,
        request.headers.get("x-twilio-signature"),
        _twilio_candidate_urls(request),
        params,
    )
    if not signed:
        if settings.require_auth:
            logger.warning("Rejected SMS webhook with missing/invalid Twilio signature.")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")
        logger.warning("SMS webhook has no valid Twilio signature; allowed because REQUIRE_AUTH is off.")

    logger.info("Incoming text received (%d chars).", len(Body))  # no phone numbers or message text
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
            statuses = [getattr(a, "delivery_status", None) or "" for a in alerts]
            failed_count = sum(1 for st in statuses if st.startswith("failed"))
            urgent_count = len(alerts) - failed_count
        else:
            serialized_alerts = []
            urgent_count = 0
            failed_count = 0
        if failed_count:
            logger.error(f"{failed_count} urgent alert(s) failed to send; they will be retried on the next check.")
        return {
            "status": "ok",
            "urgent_alerts_sent": urgent_count,
            "urgent_alerts_failed": failed_count,
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
                sync_roster(client, db)
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


# --- Email & usage analytics (DAN-200) ---
# The tracking endpoints are public (an email client can't send the access token). They only accept
# random 32-hex email ids, store no IPs or user-agents, and never return data.

_NO_STORE = {"Cache-Control": "no-store, private, max-age=0"}


def _record_email_event(
    email_id: str, event_type: str, target: Optional[str], user_agent: Optional[str], kind: Optional[str] = None
) -> None:
    if not settings.email_tracking or not is_email_id(email_id) or not hasattr(db, "record_email_event"):
        return
    # herrington.usage.v1 funnel step (DAN-233). No actor: each email goes to the whole family.
    funnel = kind if kind in FUNNELS else "email"
    step = "opened" if event_type == EVENT_OPEN else funnel_click_step(kind, target)
    emit("funnel_step", "email", funnel=funnel, step=step,
         props={"email_id": email_id, "client": client_category(user_agent)})
    try:
        db.record_email_event(email_id, event_type, target=target, client=client_category(user_agent))
    except Exception as e:
        logger.warning(f"Could not record email {event_type}: {e}")


@app.get("/t/o/{email_id}.gif", include_in_schema=False)
async def track_open(email_id: str, k: Optional[str] = Query(None), user_agent: Optional[str] = Header(None)):
    _record_email_event(email_id, EVENT_OPEN, None, user_agent, kind=k)
    return Response(content=PIXEL_GIF, media_type="image/gif", headers=_NO_STORE)


@app.get("/t/c/{email_id}", include_in_schema=False)
async def track_click(
    email_id: str, u: Optional[str] = Query(None), k: Optional[str] = Query(None),
    user_agent: Optional[str] = Header(None),
):
    target = safe_next(u)  # same-site paths only: never an open redirect
    _record_email_event(email_id, EVENT_CLICK, target, user_agent, kind=k)
    return RedirectResponse(target, status_code=302, headers=_NO_STORE)


@app.get("/api/analytics")
async def api_analytics(days: int = Query(30, ge=1, le=365)):
    """Email open/click rates and per-channel usage over the last ``days`` days."""
    from datetime import datetime, timedelta, timezone
    from dojo.tz import to_family_tz

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    if not hasattr(db, "get_analytics_rows"):
        raise HTTPException(status_code=501, detail="Analytics not supported by this storage backend.")
    rows = db.get_analytics_rows(since.isoformat(), to_family_tz(since).date().isoformat())
    return summarize(rows["sends"], rows["events"], rows["usage"], days=days, now=now)


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Entry point to launch the server with uvicorn."""
    import uvicorn
    print(f"\n🎒 Dojo Zen Web Portal launching on http://{host}:{port}")
    print(f"👉 Local access: http://localhost:{port}")
    print(f"👉 Backend: {'Firestore' if settings.use_firestore else 'SQLite'}\n")
    uvicorn.run("dojo.server:app", host=host, port=port, reload=False)
