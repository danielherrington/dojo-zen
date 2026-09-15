"""Model Context Protocol (MCP) Server for ClassDojo (Dojo Zen).

Exposes stored classroom announcements, teacher messages, school calendar,
action items, and grounded Q&A to AI coding assistants and LLMs via MCP stdio/SSE.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer

from dojo.config import settings
from dojo.db import get_database
from dojo.digest import DigestEngine, is_bloat, parse_and_format_timestamp
from dojo.qa import DojoQA

mcp = MCPServer(
    name="dojo-zen",
    title="ClassDojo Anti-Bloat MCP Server",
    instructions=(
        "Use this MCP server to query your child's ClassDojo announcements, school events, "
        "and teacher messages without opening the mobile app. All responses are anti-bloat "
        "and focused on high-signal parental logistics (what to bring, dates, assignments)."
    ),
)


def _get_db():
    settings.ensure_directories()
    return get_database()


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_daily_briefing(force: bool = False) -> str:
    """Generate the anti-bloat morning briefing for parents, summarizing high-signal action items
    (what to bring, homework, materials), upcoming calendar dates, and teacher announcements.

    Args:
        force: If True, generate the briefing even if all items were previously marked as digested.
    """
    db = _get_db()
    data = db.get_undigested_items()
    feed_items = data["feed_items"]
    messages = data["messages"]
    events = data["events"]

    total = len(feed_items) + len(messages) + len(events)
    if total == 0 and not force:
        # Fallback to recent 20 items so the briefing is always informative
        recent = db.get_all_feed_items(limit=20)
        feed_items = recent

    children = db.get_all_children() if hasattr(db, "get_all_children") else []
    engine = DigestEngine(gemini_api_key=settings.gemini_api_key)
    briefing = engine.synthesize(
        feed_items=feed_items,
        messages=messages,
        events=events,
        children=children,
    )
    return engine.format_plain_text(briefing)


@mcp.tool()
def get_action_items() -> List[Dict[str, Any]]:
    """Get all extracted action items (supplies to bring, homework packets, clothing requirements,
    permission slips) from recent teacher posts, ranked by urgency.
    """
    db = _get_db()
    recent = db.get_all_feed_items(limit=50)
    engine = DigestEngine(gemini_api_key=settings.gemini_api_key)
    briefing = engine.synthesize(feed_items=recent, messages=[], events=[], children=[])

    return [
        {
            "summary": item.summary,
            "context": item.context,
            "author": item.author,
            "urgency": item.urgency,
            "posted_at": item.posted_at_str,
            "is_stale": item.is_stale,
        }
        for item in briefing.action_items
    ]


@mcp.tool()
def get_upcoming_dates() -> List[Dict[str, Any]]:
    """List upcoming school events, early dismissals, holidays, field trips, and deadlines
    from the school calendar and teacher announcements.
    """
    db = _get_db()
    recent = db.get_all_feed_items(limit=50)
    events = db.get_all_events() if hasattr(db, "get_all_events") else []
    engine = DigestEngine(gemini_api_key=settings.gemini_api_key)
    briefing = engine.synthesize(feed_items=recent, messages=[], events=events, children=[])

    return [
        {
            "title": d.title,
            "date": d.date_str,
            "details": d.details,
            "author": d.author,
            "posted_at": d.posted_at_str,
        }
        for d in briefing.upcoming_dates
    ]


@mcp.tool()
def ask_classroom_assistant(question: str) -> Dict[str, Any]:
    """Ask a natural language question about your child's classroom, schedule, teacher instructions,
    or school activities (e.g. 'What does she need for gym?', 'When is the Hispanic Heritage project due?').

    Args:
        question: Plain English question about the school or classroom.
    """
    db = _get_db()
    qa = DojoQA(db=db, gemini_api_key=settings.gemini_api_key)
    result = qa.answer_question(question)
    return {
        "query": result.query,
        "answer": result.answer,
        "confidence": result.confidence,
        "sources": [s.model_dump() for s in result.sources],
        "suggested_followups": result.suggested_followups,
    }


@mcp.tool()
def search_feed(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search classroom announcements and stories by keyword, topic, or teacher name.

    Args:
        query: Search term (e.g., 'homework', 'sneakers', 'Alonso', 'Heritage').
        limit: Maximum number of results to return (default: 10).
    """
    db = _get_db()
    clean_q = query.strip().lower()
    raw_items = db.get_all_feed_items(limit=100)
    matches = []

    for item in raw_items:
        text = item.get("content_text") or item.get("body_text") or ""
        author = item.get("author_name", "")
        header = item.get("header", "")
        if is_bloat(text):
            continue

        if clean_q in text.lower() or clean_q in header.lower() or clean_q in author.lower():
            time_str, _ = parse_and_format_timestamp(item.get("item_timestamp"))
            matches.append({
                "id": item["id"],
                "author": author,
                "posted_at": time_str,
                "text": text[:300] + ("..." if len(text) > 300 else ""),
                "classdojo_url": f"https://home.classdojo.com/#/story/{item['id']}",
            })
            if len(matches) >= limit:
                break

    return matches


@mcp.tool()
def get_recent_feed(limit: int = 15) -> List[Dict[str, Any]]:
    """Get the most recent classroom announcements from ClassDojo with author, timestamp, content,
    and direct deep links (filtering out noisy marketing/bloat).

    Args:
        limit: Number of items to retrieve (default: 15).
    """
    db = _get_db()
    raw_items = db.get_all_feed_items(limit=limit * 2)
    clean_items = []

    for item in raw_items:
        text = item.get("content_text") or item.get("body_text") or ""
        if is_bloat(text):
            continue

        time_str, _ = parse_and_format_timestamp(item.get("item_timestamp"))
        clean_items.append({
            "id": item["id"],
            "author": item.get("author_name", "Teacher"),
            "posted_at": time_str,
            "text": text,
            "classdojo_url": f"https://home.classdojo.com/#/story/{item['id']}",
            "web_portal_url": f"https://herrington-dojo.web.app/?item={item['id']}",
        })
        if len(clean_items) >= limit:
            break

    return clean_items


@mcp.tool()
def get_feed_item(item_id: str) -> Optional[Dict[str, Any]]:
    """Get the full details of a specific ClassDojo feed announcement by its unique ID.

    Args:
        item_id: The ClassDojo item identifier.
    """
    db = _get_db()
    item = db.get_feed_item(item_id) if hasattr(db, "get_feed_item") else None
    if not item:
        return None

    time_str, _ = parse_and_format_timestamp(item.get("item_timestamp"))
    return {
        "id": item["id"],
        "author": item.get("author_name", "Teacher"),
        "posted_at": time_str,
        "text": item.get("content_text") or item.get("body_text") or "",
        "raw_time": item.get("item_timestamp"),
        "classdojo_url": f"https://home.classdojo.com/#/story/{item['id']}",
        "web_portal_url": f"https://herrington-dojo.web.app/?item={item['id']}",
    }


@mcp.tool()
def get_enrolled_children() -> List[Dict[str, Any]]:
    """List enrolled children, their student IDs, and their assigned classrooms."""
    db = _get_db()
    children = db.get_all_children() if hasattr(db, "get_all_children") else []
    classes = db.get_all_classes() if hasattr(db, "get_all_classes") else []

    class_map = {c["id"]: c.get("name", "Unknown Class") for c in classes}
    result = []
    for child in children:
        c_id = child.get("id")
        name = child.get("name") or f"{child.get('first_name', '')} {child.get('last_name', '')}".strip() or "Child"
        result.append({
            "id": c_id,
            "name": name,
            "class_name": class_map.get(child.get("class_id"), "General Classroom"),
        })
    return result


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------

@mcp.resource("dojo://briefing/today")
def resource_today_briefing() -> str:
    """Provides today's synthesized morning briefing as a readable text document."""
    return get_daily_briefing(force=False)


@mcp.resource("dojo://action-items")
def resource_action_items() -> str:
    """Provides all active parental action items as a markdown list."""
    items = get_action_items()
    if not items:
        return "No active action items currently found."
    lines = ["# 🎒 Active ClassDojo Action Items\n"]
    for i, it in enumerate(items, 1):
        urgency_badge = "🚨 [URGENT]" if it["urgency"] == "high" else "📌"
        lines.append(f"{i}. {urgency_badge} **{it['summary']}**")
        lines.append(f"   * Context: {it['context']}")
        lines.append(f"   * Posted by: {it['author']} ({it['posted_at']})\n")
    return "\n".join(lines)


@mcp.resource("dojo://calendar/upcoming")
def resource_upcoming_calendar() -> str:
    """Provides upcoming school events and calendar dates as markdown."""
    dates = get_upcoming_dates()
    if not dates:
        return "No upcoming calendar dates found."
    lines = ["# 📅 Upcoming School Events & Dates\n"]
    for d in dates:
        lines.append(f"* **{d['date']}**: {d['title']}")
        if d.get("details"):
            lines.append(f"  * Details: {d['details']}")
        if d.get("author"):
            lines.append(f"  * Posted by: {d['author']}")
    return "\n".join(lines)


@mcp.resource("dojo://feed/recent")
def resource_recent_feed() -> str:
    """Provides the recent clean feed announcements as markdown."""
    posts = get_recent_feed(limit=10)
    lines = ["# 📣 Recent Classroom Announcements\n"]
    for p in posts:
        lines.append(f"### {p['author']} — {p['posted_at']}")
        lines.append(f"{p['text']}\n")
        lines.append(f"[Open in ClassDojo]({p['classdojo_url']}) | [Open in Dojo Zen]({p['web_portal_url']})\n---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Prompts
# ---------------------------------------------------------------------------

@mcp.prompt("weekly-school-prep")
def prompt_weekly_prep() -> str:
    """Generate a structured Monday-to-Friday preparation checklist for the family based on all current ClassDojo items."""
    return (
        "Please review the ClassDojo announcements and active action items using the `get_action_items` "
        "and `get_upcoming_dates` tools. Then, organize a clear, structured Monday through Friday "
        "preparation checklist for our family (what to pack, wear, homework due dates, and early dismissals)."
    )


@mcp.prompt("urgent-logistics-check")
def prompt_urgent_check() -> str:
    """Analyze recent ClassDojo posts to check for any urgent same-day logistics, schedule changes, or immediate parental actions."""
    return (
        "Check recent ClassDojo posts and messages using `get_recent_feed` and `get_action_items`. "
        "Determine if there are any urgent same-day logistics (early pickup, bus change, sick office notices, "
        "or emergency supplies needed tomorrow) that require immediate parental attention."
    )


def run_mcp(transport: str = "stdio", **kwargs: Any) -> None:
    """Run the ClassDojo MCP server."""
    mcp.run(transport=transport, **kwargs)


if __name__ == "__main__":
    run_mcp("stdio")
