"""Unit tests for ClassDojo MCP Server."""

import pytest
from dojo.mcp_server import (
    mcp,
    get_daily_briefing,
    get_action_items,
    get_upcoming_dates,
    ask_classroom_assistant,
    search_feed,
    get_recent_feed,
    get_feed_item,
    get_enrolled_children,
    resource_today_briefing,
    resource_action_items,
    resource_upcoming_calendar,
    resource_recent_feed,
    prompt_weekly_prep,
    prompt_urgent_check,
)


@pytest.fixture
def mock_populated_db(monkeypatch, tmp_path):
    from dojo.db import DojoDatabase
    test_db = DojoDatabase(tmp_path / "test_mcp.db")

    # Seed test data
    test_db.upsert_child("child-1", "Isabella", "1st Grade")
    test_db.upsert_class("class-1", "Ms. Alonso's Class", "Ms. Alonso", "child-1")

    test_db.upsert_feed_items([
        {
            "_id": "post-1",
            "header": "Gym Tomorrow",
            "body": "Please make sure students wear sneakers tomorrow for gym class.",
            "time": "2026-09-14T12:00:00Z",
            "sender": {"name": "Ms. Alonso"},
            "target": {"name": "Ms. Alonso's Class"},
        },
        {
            "_id": "post-2",
            "header": "Hispanic Heritage Project",
            "body": "Due next Wednesday! Students will research and present a poster board.",
            "time": "2026-09-14T14:00:00Z",
            "sender": {"name": "Ms. Alonso"},
            "target": {"name": "Ms. Alonso's Class"},
        }
    ])

    test_db.upsert_events([
        {
            "_id": "ev-1",
            "title": "Early Dismissal",
            "start": "2026-09-18T13:15:00Z",
            "end": "2026-09-18T14:00:00Z",
            "description": "Dismissal at 1:15 PM for teacher planning.",
        }
    ])

    monkeypatch.setattr("dojo.mcp_server._get_db", lambda: test_db)
    return test_db


@pytest.mark.anyio
async def test_mcp_registration():
    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "get_daily_briefing" in tool_names
    assert "get_action_items" in tool_names
    assert "get_upcoming_dates" in tool_names
    assert "ask_classroom_assistant" in tool_names
    assert "search_feed" in tool_names
    assert "get_recent_feed" in tool_names
    assert "get_feed_item" in tool_names
    assert "get_enrolled_children" in tool_names

    resources = await mcp.list_resources()
    uris = [r.uri for r in resources]
    assert "dojo://briefing/today" in uris
    assert "dojo://action-items" in uris
    assert "dojo://calendar/upcoming" in uris
    assert "dojo://feed/recent" in uris

    prompts = await mcp.list_prompts()
    prompt_names = [p.name for p in prompts]
    assert "weekly-school-prep" in prompt_names
    assert "urgent-logistics-check" in prompt_names


def test_mcp_tools_execution(mock_populated_db):
    # Action items
    items = get_action_items()
    assert len(items) >= 1
    summaries = [it["summary"] for it in items]
    assert any("sneakers" in s.lower() or "gym" in s.lower() or "due" in s.lower() for s in summaries)

    # Upcoming dates
    dates = get_upcoming_dates()
    assert len(dates) >= 1
    assert any("Early Dismissal" in d["title"] for d in dates)

    # Q&A Assistant
    ans = ask_classroom_assistant("What should Isabella wear for gym?")
    assert "query" in ans
    assert "answer" in ans
    assert "sneakers" in ans["answer"].lower() or len(ans["sources"]) > 0

    # Search feed
    search_results = search_feed("Heritage")
    assert len(search_results) >= 1
    assert "post-2" in [r["id"] for r in search_results]

    # Recent feed
    recent = get_recent_feed(limit=5)
    assert len(recent) >= 2
    assert any(r["id"] == "post-1" for r in recent)

    # Single feed item
    item = get_feed_item("post-1")
    assert item is not None
    assert item["id"] == "post-1"
    assert "sneakers" in item["text"].lower()

    # Enrolled children
    children = get_enrolled_children()
    assert len(children) == 1
    assert children[0]["name"] == "Isabella"

    # Daily briefing
    briefing = get_daily_briefing(force=True)
    assert "DOJOZEN DAILY BRIEFING" in briefing


def test_mcp_resources_and_prompts(mock_populated_db):
    res_briefing = resource_today_briefing()
    assert "DOJOZEN DAILY BRIEFING" in res_briefing

    res_actions = resource_action_items()
    assert "Active ClassDojo Action Items" in res_actions

    res_cal = resource_upcoming_calendar()
    assert "Upcoming School Events" in res_cal

    res_feed = resource_recent_feed()
    assert "Recent Classroom Announcements" in res_feed

    p_prep = prompt_weekly_prep()
    assert "preparation checklist" in p_prep.lower()

    p_urgent = prompt_urgent_check()
    assert "urgent" in p_urgent.lower()
