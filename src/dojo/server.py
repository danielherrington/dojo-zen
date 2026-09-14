"""FastAPI web server for the Dojo Zen Web Portal & Q&A Assistant."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dojo.config import settings
from dojo.db import DojoDatabase
from dojo.client import DojoClient
from dojo.digest import DigestEngine, parse_and_format_timestamp
from dojo.qa import DojoQA, QAResult

app = FastAPI(title="Dojo Zen Web Portal", description="Classroom Assistant for Daniel & Family")

db = DojoDatabase(settings.dojo_db_path)
qa_engine = DojoQA(db, gemini_api_key=settings.gemini_api_key)
digest_engine = DigestEngine(gemini_api_key=settings.gemini_api_key)

WEB_DIR = Path(__file__).parent / "web"


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
    with db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM feed_items ORDER BY item_timestamp DESC LIMIT 50").fetchall()
        feed_items = [dict(r) for r in rows]

        msg_rows = conn.execute("SELECT * FROM messages ORDER BY message_timestamp DESC LIMIT 20").fetchall()
        messages = [dict(r) for r in msg_rows]

        event_rows = conn.execute("SELECT * FROM events ORDER BY start_time ASC").fetchall()
        events = [dict(r) for r in event_rows]

        child_rows = conn.execute("SELECT * FROM children").fetchall()
        children = [dict(r) for r in child_rows]

    briefing = digest_engine.synthesize(feed_items, messages, events, children)

    # Return active items and highlights
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
    """Retrieve classroom feed with formatted local timestamps and authors."""
    items = []
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM feed_items ORDER BY item_timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        for r in rows:
            text = (r["content_text"] or r["header"] or "").strip()
            if not text:
                continue
            posted_str, _ = parse_and_format_timestamp(r["item_timestamp"])
            items.append({
                "id": r["id"],
                "author": r["author_name"] or "School",
                "header": r["header"],
                "text": text,
                "posted_at_str": posted_str,
                "raw_time": r["item_timestamp"]
            })
    return {"items": items}


@app.post("/api/sync")
async def sync_now():
    """Trigger an on-demand sync from ClassDojo."""
    if not settings.dojo_session_file.exists():
        raise HTTPException(status_code=401, detail="No active session found. Please log in first.")

    try:
        client = DojoClient(session_file=settings.dojo_session_file)
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
        "database_path": str(settings.dojo_db_path),
    }


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Entry point to launch the server with uvicorn."""
    import uvicorn
    print(f"\n🎒 Dojo Zen Web Portal launching on http://{host}:{port}")
    print(f"👉 Local access: http://localhost:{port}")
    print(f"👉 Mobile / Family access: http://<your-mac-ip>:{port}\n")
    uvicorn.run("dojo.server:app", host=host, port=port, reload=False)
