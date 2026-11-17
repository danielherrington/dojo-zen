"""School & Family Logistics (DAN-251): read-only views over Dojo Zen data and the Family calendars.

Three questions, each answered with the minimum a conversation needs:

* ``school_today``: per child, today's school events and action items due today/tomorrow.
* ``school_upcoming``: dated items in the next N days, typed day_off / early_dismissal / deadline / event.
* ``pickup_owner``: who has pickup/drop-off on a date. The owner is the Family calendar the event
  instance sits on (read from each calendar's private iCal feed, recurring events expanded).

Privacy: this repo is public, so children, schools and calendar feeds come from env, never code.
No message bodies, descriptions, photos, attachments or sender names are returned. ``pickup_owner``
refuses on Cloud Run unless REQUIRE_AUTH is on: pickup times are safety-sensitive.

Config (JSON env vars):
    FAMILY_SCHOOLS='[{"child": "Izzy", "school": "North Beach Elementary", "source": "dojo"},
                     {"child": "Vale", "school": "Lehrman", "source": "lehrman"}]'
    FAMILY_PICKUP_CALENDARS='{"Daniel": "https://calendar.google.com/calendar/ical/.../basic.ics", ...}'
"""

import json
import logging
import os
import re
import time
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from dojo.tz import family_now, family_tz, to_family_tz

logger = logging.getLogger(__name__)

CONNECTED_SOURCES = {"dojo"}

_DAY_OFF = re.compile(r"\b(no school|school (is )?closed|closed|holiday|teacher (planning|work ?day)|"
                      r"professional development|in-?service|winter break|spring break|thanksgiving break)\b", re.I)
_EARLY = re.compile(r"\b(early dismissal|early release|half[- ]day|minimum day)\b", re.I)
_DEADLINE = re.compile(r"\b(due|deadline|return (by|the)|last day to|sign[- ]?up by|rsvp|turn in)\b", re.I)
_PICKUP = re.compile(r"\b(pick[- ]?up|carpool|dismissal)\b", re.I)
_DROPOFF = re.compile(r"\b(drop[- ]?off|school run)\b", re.I)


# --- Config ---

def _json_env(name: str, default: Any) -> Any:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"{name} is not valid JSON; ignoring it.")
        return default


def family_schools(db: Any) -> List[Dict[str, str]]:
    """Configured children, or the Dojo roster (first names) when FAMILY_SCHOOLS is unset."""
    configured = _json_env("FAMILY_SCHOOLS", None)
    if isinstance(configured, list) and configured:
        return [
            {"child": str(c.get("child", "Child")), "school": str(c.get("school", "")), "source": str(c.get("source", "dojo"))}
            for c in configured if isinstance(c, dict)
        ]
    roster = db.get_all_children() if hasattr(db, "get_all_children") else []
    out = []
    for c in roster:
        name = (c.get("name") or c.get("first_name") or "Child").split()[0]
        out.append({"child": name, "school": "", "source": "dojo"})
    return out


def pickup_calendars() -> Dict[str, str]:
    cals = _json_env("FAMILY_PICKUP_CALENDARS", {})
    return {str(k): str(v) for k, v in cals.items()} if isinstance(cals, dict) else {}


def _parse_day(value: Optional[str]) -> date_cls:
    if not value or value.lower() == "today":
        return family_now().date()
    if value.lower() == "tomorrow":
        return family_now().date() + timedelta(days=1)
    return date_cls.fromisoformat(value)


# --- Classification ---

def classify(title: str) -> str:
    if _EARLY.search(title):
        return "early_dismissal"
    if _DAY_OFF.search(title):
        return "day_off"
    if _DEADLINE.search(title):
        return "deadline"
    return "event"


def _event_day(ev: Dict[str, Any]) -> Optional[date_cls]:
    from dojo.digest import parse_raw_datetime

    raw = ev.get("start_time") or ev.get("start")
    if not raw:
        return None
    if isinstance(raw, str) and len(raw) == 10:
        try:
            return date_cls.fromisoformat(raw)
        except ValueError:
            return None
    dt = parse_raw_datetime(raw)
    return to_family_tz(dt, naive_tz=family_tz()).date() if dt else None


def _dojo_events_between(db: Any, start: date_cls, end: date_cls) -> List[Dict[str, Any]]:
    events = db.get_all_events() if hasattr(db, "get_all_events") else []
    out = []
    for ev in events:
        day = _event_day(ev)
        title = (ev.get("title") or "").strip()
        if day and title and start <= day <= end:
            out.append({"date": day.isoformat(), "title": title, "type": classify(title)})
    return sorted(out, key=lambda e: e["date"])


# --- Tools ---

def school_today(db: Any, day: Optional[str] = None) -> List[Dict[str, Any]]:
    from dojo.config import settings
    from dojo.digest import DigestEngine, collect_briefing_inputs

    target = _parse_day(day)
    kids = family_schools(db)
    dojo_summary: Optional[Dict[str, Any]] = None
    if any(k["source"] == "dojo" for k in kids):
        inputs = collect_briefing_inputs(db)
        ref = datetime.combine(target, datetime.min.time().replace(hour=7), tzinfo=family_tz())
        briefing = DigestEngine(gemini_api_key=settings.gemini_api_key).synthesize(
            feed_items=inputs["feed_items"], messages=inputs["messages"], events=inputs["events"],
            children=inputs["children"], ref_dt=ref, daily_focus=True, respect_digested=False,
        )
        dojo_summary = {
            "events": _dojo_events_between(db, target, target),
            "action_items": [
                {"summary": a.summary, "urgency": a.urgency} for a in briefing.active_action_items
            ],
        }

    out = []
    for kid in kids:
        entry = {"child": kid["child"], "school": kid["school"], "date": target.isoformat(), "source": kid["source"]}
        if kid["source"] == "dojo" and dojo_summary is not None:
            entry.update(status="ok", **dojo_summary)
        else:
            entry.update(status="not_connected",
                         note=f"No data source for '{kid['source']}' yet; nothing is known for this child today.")
        out.append(entry)
    return out


def school_upcoming(db: Any, days: int = 14, start: Optional[str] = None) -> Dict[str, Any]:
    from dojo.config import settings
    from dojo.digest import DigestEngine

    days = max(1, min(int(days), 60))
    first = _parse_day(start)
    last = first + timedelta(days=days)
    items = [dict(e, source="dojo") for e in _dojo_events_between(db, first, last)]

    # Dates teachers mention in posts (free-form date text from the digest, title only).
    briefing = DigestEngine(gemini_api_key=settings.gemini_api_key).synthesize(
        feed_items=db.get_all_feed_items(limit=50),
        messages=db.get_all_messages(limit=30) if hasattr(db, "get_all_messages") else [],
        events=[], children=[], daily_focus=False,
    )
    seen = {e["title"].lower() for e in items}
    for d in briefing.active_upcoming_dates:
        if d.title.lower() in seen:
            continue
        items.append({"date": None, "date_text": d.date_str, "title": d.title, "type": classify(d.title),
                      "source": "dojo_posts"})

    not_connected = sorted({k["source"] for k in family_schools(db)} - CONNECTED_SOURCES)
    return {"from": first.isoformat(), "to": last.isoformat(), "items": items, "sources_not_connected": not_connected}


# --- Pickup ownership from the Family calendars' iCal feeds ---

_ICS_CACHE: Dict[str, tuple] = {}
ICS_TTL_SECONDS = 600


def _fetch_ics(url: str) -> str:
    import httpx

    hit = _ICS_CACHE.get(url)
    if hit and time.monotonic() - hit[0] < ICS_TTL_SECONDS:
        return hit[1]
    resp = httpx.get(url, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    _ICS_CACHE[url] = (time.monotonic(), resp.text)
    return resp.text


def pickup_disabled_reason() -> Optional[str]:
    from dojo.auth import is_cloud_run
    from dojo.config import settings

    if is_cloud_run() and not settings.require_auth:
        return "pickup_owner is disabled on the public endpoint until REQUIRE_AUTH is on (pickup times are safety-sensitive)."
    return None


def pickup_owner(
    day: Optional[str] = None,
    kind: str = "any",
    calendars: Optional[Dict[str, str]] = None,
    fetch: Callable[[str], str] = _fetch_ics,
    children: Optional[List[str]] = None,
) -> Dict[str, Any]:
    import icalendar
    import recurring_ical_events

    target = _parse_day(day)
    reason = pickup_disabled_reason()
    if reason:
        return {"date": target.isoformat(), "status": "disabled", "reason": reason, "assignments": []}
    calendars = pickup_calendars() if calendars is None else calendars
    if not calendars:
        return {"date": target.isoformat(), "status": "not_connected", "assignments": [],
                "reason": "No Family calendars configured (FAMILY_PICKUP_CALENDARS)."}

    labels = children or []
    assignments, errors = [], []
    for owner, url in calendars.items():
        try:
            cal = icalendar.Calendar.from_ical(fetch(url))
            instances = recurring_ical_events.of(cal).at(target)
        except Exception as e:
            logger.warning(f"Could not read the {owner} calendar: {type(e).__name__}")
            errors.append(owner)
            continue
        for ev in instances:
            summary = str(ev.get("SUMMARY", ""))
            ev_kind = "dropoff" if _DROPOFF.search(summary) else ("pickup" if _PICKUP.search(summary) else None)
            if ev_kind is None or (kind != "any" and ev_kind != kind):
                continue
            start, end = ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None
            entry = {"owner": owner, "kind": ev_kind, "start": _hhmm(start), "end": _hhmm(end)}
            named = [c for c in labels if re.search(rf"\b{re.escape(c)}\b", summary, re.I)]
            if named:
                entry["children"] = named
            assignments.append(entry)

    assignments.sort(key=lambda a: (a["start"] or "", a["owner"]))
    result = {"date": target.isoformat(), "status": "ok", "assignments": assignments}
    if not assignments:
        result["note"] = "No pickup or drop-off events on any Family calendar for this date."
    if errors:
        result["calendars_unreachable"] = errors
    return result


def _hhmm(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return to_family_tz(value, naive_tz=family_tz()).strftime("%H:%M")
    return None  # all-day


# --- Catalog metadata (DAN-244 design principles) ---

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "mcp_catalog.json")


def catalog_entry(db: Any = None) -> Dict[str, Any]:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        entry = json.load(f)
    if db is not None and hasattr(db, "get_stats"):
        try:
            entry["last_health_check"] = db.get_stats().get("last_sync")
        except Exception:
            entry["last_health_check"] = None
    return entry
