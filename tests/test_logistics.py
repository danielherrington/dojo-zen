"""School & Family Logistics MCP tools (DAN-251)."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dojo import logistics
from dojo.db import DojoDatabase

SCHOOLS = json.dumps([
    {"child": "Kid A", "school": "Test Elementary", "source": "dojo"},
    {"child": "Kid B", "school": "Test Academy", "source": "lehrman"},
])

DANIEL_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:pickup-weekly@test
DTSTART;TZID=America/New_York:20261116T151500
DTEND;TZID=America/New_York:20261116T154500
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
EXDATE;TZID=America/New_York:20261120T151500
SUMMARY:Kid A pickup
DESCRIPTION:gate code 1234
END:VEVENT
BEGIN:VEVENT
UID:dentist@test
DTSTART;TZID=America/New_York:20261120T100000
DTEND;TZID=America/New_York:20261120T110000
SUMMARY:Dentist
END:VEVENT
END:VCALENDAR
"""

LU_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:pickup-friday@test
DTSTART;TZID=America/New_York:20261120T151500
DTEND;TZID=America/New_York:20261120T154500
SUMMARY:Kid A pick-up
END:VEVENT
BEGIN:VEVENT
UID:dropoff@test
DTSTART;TZID=America/New_York:20261120T075000
DTEND;TZID=America/New_York:20261120T081500
SUMMARY:School drop-off
END:VEVENT
END:VCALENDAR
"""

CALENDARS = {"Daniel": "https://cal.test/daniel.ics", "Lu": "https://cal.test/lu.ics"}
FEEDS = {"https://cal.test/daniel.ics": DANIEL_ICS, "https://cal.test/lu.ics": LU_ICS}


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = DojoDatabase(Path(tmpdir) / "logistics.db")
        d.upsert_child("c1", "Kid A Surname", "1st Grade")
        d.upsert_events([
            {"_id": "e1", "title": "Grandparent Day", "start": "2026-11-18T14:00:00Z",
             "description": "Private note about a classmate"},
            {"_id": "e2", "title": "No School - Teacher Planning", "start": "2026-11-20T13:00:00Z"},
            {"_id": "e3", "title": "Early Dismissal", "start": "2026-11-25T17:00:00Z"},
            {"_id": "e4", "title": "Book report due", "start": "2026-11-19T13:00:00Z"},
            {"_id": "e5", "title": "Winter Concert", "start": "2027-01-15T23:00:00Z"},
        ])
        yield d


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("FAMILY_SCHOOLS", SCHOOLS)
    logistics._ICS_CACHE.clear()


# --- MCP surface ---

@pytest.mark.anyio
async def test_logistics_tools_registered_alongside_existing_ones():
    from dojo.mcp_server import mcp
    names = {t.name for t in await mcp.list_tools()}
    assert {"school_today", "school_upcoming", "pickup_owner"} <= names
    assert {"get_daily_briefing", "get_action_items", "get_upcoming_dates", "ask_classroom_assistant",
            "search_feed", "get_recent_feed", "get_feed_item", "get_enrolled_children"} <= names


@pytest.mark.parametrize("title,kind", [
    ("No School - Teacher Planning", "day_off"),
    ("Thanksgiving Break", "day_off"),
    ("Early Dismissal 12:30", "early_dismissal"),
    ("Book report due", "deadline"),
    ("Return the permission slip by Friday", "deadline"),
    ("Grandparent Day", "event"),
    ("Walking Club", "event"),
])
def test_classify(title, kind):
    assert logistics.classify(title) == kind


# --- school_today ---

def test_school_today_per_child(db):
    out = logistics.school_today(db, "2026-11-18")
    assert [(c["child"], c["school"], c["status"]) for c in out] == [
        ("Kid A", "Test Elementary", "ok"), ("Kid B", "Test Academy", "not_connected")]
    assert out[0]["events"] == [{"date": "2026-11-18", "title": "Grandparent Day", "type": "event"}]
    assert isinstance(out[0]["action_items"], list)
    assert "classmate" not in json.dumps(out)  # descriptions never leave


def test_school_today_defaults_to_roster_first_names(db, monkeypatch):
    monkeypatch.delenv("FAMILY_SCHOOLS")
    out = logistics.school_today(db, "2026-11-18")
    assert [(c["child"], c["status"]) for c in out] == [("Kid", "ok")]
    assert "Surname" not in json.dumps(out)


def test_invalid_family_schools_falls_back_to_roster(db, monkeypatch):
    monkeypatch.setenv("FAMILY_SCHOOLS", "{not json")
    assert [k["source"] for k in logistics.family_schools(db)] == ["dojo"]


# --- school_upcoming ---

def test_school_upcoming_window_and_types(db):
    out = logistics.school_upcoming(db, days=14, start="2026-11-17")
    dated = [(i["date"], i["title"], i["type"]) for i in out["items"] if i["date"]]
    assert dated == [
        ("2026-11-18", "Grandparent Day", "event"),
        ("2026-11-19", "Book report due", "deadline"),
        ("2026-11-20", "No School - Teacher Planning", "day_off"),
        ("2026-11-25", "Early Dismissal", "early_dismissal"),
    ]  # Winter Concert is outside the window
    assert out["sources_not_connected"] == ["lehrman"]
    assert "classmate" not in json.dumps(out)


def test_school_upcoming_clamps_days(db):
    out = logistics.school_upcoming(db, days=999, start="2026-11-17")
    assert out["to"] == "2027-01-16"


# --- pickup_owner ---

def _pickup(day, **kw):
    return logistics.pickup_owner(day, calendars=CALENDARS, fetch=FEEDS.__getitem__, children=["Kid A", "Kid B"], **kw)


def test_pickup_owner_from_recurring_calendar():
    out = _pickup("2026-11-18")
    assert out["status"] == "ok"
    assert out["assignments"] == [
        {"owner": "Daniel", "kind": "pickup", "start": "15:15", "end": "15:45", "children": ["Kid A"]}]


def test_pickup_owner_follows_the_calendar_the_instance_sits_on():
    out = _pickup("2026-11-20")  # Daniel's series skips Friday; Lu's calendar has that instance
    assert [(a["owner"], a["kind"], a["start"]) for a in out["assignments"]] == [
        ("Lu", "dropoff", "07:50"), ("Lu", "pickup", "15:15")]
    assert "gate code" not in json.dumps(out) and "Dentist" not in json.dumps(out)


def test_pickup_owner_kind_filter():
    out = _pickup("2026-11-20", kind="dropoff")
    assert [(a["owner"], a["kind"]) for a in out["assignments"]] == [("Lu", "dropoff")]


def test_pickup_owner_weekend_has_no_assignments():
    out = _pickup("2026-11-21")
    assert out["assignments"] == [] and "note" in out


def test_pickup_owner_reports_unreachable_calendar():
    def fetch(url):
        if "lu" in url:
            raise ConnectionError("boom")
        return DANIEL_ICS
    out = logistics.pickup_owner("2026-11-18", calendars=CALENDARS, fetch=fetch)
    assert out["calendars_unreachable"] == ["Lu"]
    assert [a["owner"] for a in out["assignments"]] == ["Daniel"]


def test_pickup_owner_not_connected_without_calendars(monkeypatch):
    monkeypatch.delenv("FAMILY_PICKUP_CALENDARS", raising=False)
    assert logistics.pickup_owner("2026-11-18")["status"] == "not_connected"


def test_pickup_owner_refuses_on_public_endpoint_without_auth(monkeypatch):
    from dojo.config import settings
    monkeypatch.setenv("K_SERVICE", "dojo-zen")
    monkeypatch.setattr(settings, "require_auth", False)
    out = _pickup("2026-11-18")
    assert out["status"] == "disabled" and out["assignments"] == []
    monkeypatch.setattr(settings, "require_auth", True)
    assert _pickup("2026-11-18")["status"] == "ok"


def test_pickup_calendars_env(monkeypatch):
    monkeypatch.setenv("FAMILY_PICKUP_CALENDARS", json.dumps(CALENDARS))
    assert logistics.pickup_calendars() == CALENDARS


# --- Catalog metadata ---

def test_catalog_endpoint(db, monkeypatch):
    from dojo import server
    monkeypatch.setattr(server, "db", db)
    body = TestClient(server.app).get("/.well-known/mcp-catalog.json").json()
    assert body["access"] == "read-only"
    assert body["approval_requirements"].startswith("none")
    assert {"school_today", "school_upcoming", "pickup_owner"} <= {t["name"] for t in body["tools"]}
    assert all(t["access"] == "read" for t in body["tools"])
    assert "last_health_check" in body and body["auth_model"]


# --- DAN-396: Family Schedule Helper weekly plan (synthetic family only) ---

SETUP = {
    "caregivers": [
        {"id": "cg1", "name": "Parent One", "calendarId": "p1@test"},
        {"id": "cg2", "name": "Parent Two", "calendarId": "p2@test"},
        {"id": "cg3", "name": "Sitter Three", "calendarId": "s3@test"},
    ],
    "children": [
        {"id": "k1", "name": "Kid A", "school": "Test Elementary"},
        {"id": "k2", "name": "Kid B", "school": "Lehrman Test Academy"},
        {"id": "dog", "name": "Rex", "school": "", "isPet": True},
    ],
    "blueprints": [
        {"id": "b1", "title": "Kid A drop-off", "childId": "k1", "category": "dropoff", "daysOfWeek": [1, 2, 3, 4, 5],
         "startTime": "07:50", "endTime": "08:15", "location": "1 Secret Lane", "defaultCaregiverId": "cg1"},
        {"id": "b2", "title": "Kid A pickup", "childId": "k1", "category": "pickup", "daysOfWeek": [5],
         "startTime": "15:15", "endTime": "15:45", "location": "1 Secret Lane", "defaultCaregiverId": "cg2",
         "notes": "gate code 1234"},
        {"id": "b3", "title": "Kid B pickup", "childId": "k2", "category": "pickup", "daysOfWeek": [1],
         "startTime": "15:00", "endTime": "15:30", "location": "2 Hidden Rd", "defaultCaregiverId": "cg2"},
        {"id": "b4", "title": "Swim", "childId": "k2", "category": "activity", "daysOfWeek": [5],
         "startTime": "17:00", "endTime": "18:00", "location": "Pool", "defaultCaregiverId": "cg1"},
        {"id": "b5", "title": "Dog walk", "childId": "dog", "category": "pickup", "daysOfWeek": [5],
         "startTime": "12:00", "endTime": "12:30", "location": "Park", "defaultCaregiverId": "cg3"},
    ],
    "travels": [{"id": "t1", "caregiverId": "cg2", "routeToCaregiverId": "cg3",
                 "startDate": "2026-11-27", "endDate": "2026-11-29"}],
}


@pytest.fixture
def no_overrides(monkeypatch):
    for name in ("FAMILY_SCHOOLS", "FAMILY_PICKUP_CALENDARS", "FAMILY_SETUP_PROJECT", "K_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    logistics._SETUP_CACHE.clear()


def test_weekly_plan_friday_owners_without_locations_or_notes(no_overrides):
    out = logistics.pickup_owner("2026-11-20", calendars={}, setup=SETUP)  # a Friday
    assert out["status"] == "ok" and out["source"] == "weekly_plan"
    assert [(a["owner"], a["kind"], a["start"], a["children"]) for a in out["assignments"]] == [
        ("Parent", "dropoff", "07:50", ["Kid"]), ("Parent", "pickup", "15:15", ["Kid"])]
    text = json.dumps(out)
    assert "Secret Lane" not in text and "1234" not in text and "Rex" not in text and "Swim" not in text
    assert "swaps" in out["note"]


def test_weekly_plan_kind_filter_and_travel_cover(no_overrides):
    out = logistics.pickup_owner("2026-11-27", kind="pickup", calendars={}, setup=SETUP)  # Friday, cg2 travelling
    (only,) = out["assignments"]
    assert only["owner"] == "Sitter" and only["covering_for"] == "Parent"


def test_weekly_plan_empty_day(no_overrides):
    out = logistics.pickup_owner("2026-11-22", calendars={}, setup=SETUP)  # Sunday
    assert out["assignments"] == [] and out["note"].startswith("No pickup or drop-off")


def test_ical_feeds_win_over_the_weekly_plan(no_overrides):
    out = logistics.pickup_owner("2026-11-20", calendars=CALENDARS, fetch=FEEDS.__getitem__, setup=SETUP)
    assert "source" not in out and {a["owner"] for a in out["assignments"]} <= {"Daniel", "Lu"}


def test_setup_project_defaults_and_off(no_overrides, monkeypatch):
    monkeypatch.delenv("USE_FIRESTORE", raising=False)
    assert logistics.setup_project() is None
    monkeypatch.setenv("USE_FIRESTORE", "true")
    assert logistics.setup_project() == "family-schedule-helper"
    monkeypatch.setenv("FAMILY_SETUP_PROJECT", "off")
    assert logistics.setup_project() is None


def test_family_setup_caches_and_survives_errors(no_overrides, monkeypatch):
    monkeypatch.setenv("FAMILY_SETUP_PROJECT", "proj-test")
    calls = []
    assert logistics.family_setup(lambda p: calls.append(p) or {"children": []}) == {"children": []}
    assert logistics.family_setup(lambda p: calls.append(p) or {}) == {"children": []}  # cached
    assert calls == ["proj-test"]
    logistics._SETUP_CACHE.clear()

    def boom(_):
        raise PermissionError("denied")
    assert logistics.family_setup(boom) is None
