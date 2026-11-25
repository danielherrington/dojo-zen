"""School & Family Logistics (DAN-251): read-only views over Dojo Zen data and the Family calendars.

Three questions, each answered with the minimum a conversation needs:

* ``school_today``: per child, today's school events and action items due today/tomorrow.
* ``school_upcoming``: dated items in the next N days, typed day_off / early_dismissal / deadline / event.
* ``pickup_owner``: who has pickup/drop-off on a date. Two sources, first match wins:
  1. FAMILY_PICKUP_CALENDARS (private iCal feeds): the owner is the calendar the instance sits on.
  2. The Family Schedule Helper's ``family/setup`` Firestore doc (DAN-396): the weekly plan's default
     caregiver for each pickup/drop-off, rerouted by travel cover. One-day swaps made in the helper
     live only on Google Calendar and are not visible from this source.

Lehrman (DAN-395) is email only: ``dojo.school_email`` syncs dated items from its emails, and
``school_*`` treat the ``lehrman`` source as connected once that Gmail sync is configured.

Privacy: this repo is public, so children, schools and calendar feeds come from env, never code.
No message bodies, descriptions, photos, attachments or sender names are returned. ``pickup_owner``
refuses on Cloud Run unless REQUIRE_AUTH is on: pickup times are safety-sensitive.

Config: children and schools come from FAMILY_SCHOOLS, else the ClassDojo roster. The helper doc
is read from project FAMILY_SETUP_PROJECT (default ``family-schedule-helper`` when USE_FIRESTORE is
on; the runtime account has read-only roles/datastore.viewer there).

JSON env vars (optional overrides):
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


def connected_sources() -> set:
    """ClassDojo, plus Lehrman once its read-only Gmail sync is configured (DAN-395)."""
    from dojo import school_email

    return CONNECTED_SOURCES | ({school_email.SOURCE} if school_email.gmail_configured() else set())


def _email_items(db: Any, source: str, start: date_cls, end: date_cls) -> List[Dict[str, Any]]:
    if not hasattr(db, "get_school_items"):
        return []
    rows = db.get_school_items(source, start.isoformat(), end.isoformat())
    return [{"date": r["date"], "title": r["title"], "type": r["type"]} for r in rows]

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


# --- Family Schedule Helper (family/setup in its own Firestore project, read-only) ---

SETUP_TTL_SECONDS = 600
_SETUP_CACHE: Dict[str, Any] = {}


def setup_project() -> Optional[str]:
    explicit = os.getenv("FAMILY_SETUP_PROJECT", "").strip()
    if explicit:
        return None if explicit.lower() in {"off", "none", "false"} else explicit
    return "family-schedule-helper" if os.getenv("USE_FIRESTORE", "").lower() == "true" else None


def _read_setup_doc(project: str) -> Optional[Dict[str, Any]]:
    from google.cloud import firestore

    snap = firestore.Client(project=project).collection("family").document("setup").get()
    return snap.to_dict() if snap.exists else None


def family_setup(loader: Callable[[str], Optional[Dict[str, Any]]] = _read_setup_doc) -> Optional[Dict[str, Any]]:
    """The helper's family/setup doc (cached), or None when not configured or unreadable."""
    project = setup_project()
    if not project:
        return None
    hit = _SETUP_CACHE.get(project)
    if hit and time.monotonic() - hit[0] < SETUP_TTL_SECONDS:
        return hit[1]
    try:
        doc = loader(project)
    except Exception as e:
        logger.warning(f"Could not read the Family Schedule Helper setup: {type(e).__name__}")
        return None
    _SETUP_CACHE[project] = (time.monotonic(), doc)
    return doc


def _first(name: Any, default: str) -> str:
    parts = str(name or "").split()
    return parts[0] if parts else default


def family_schools(db: Any) -> List[Dict[str, str]]:
    """Configured children, or the Dojo roster (first names) when FAMILY_SCHOOLS is unset.

    Not taken from the helper: its children list mixes in pets and its schools don't yet agree with
    the ClassDojo roster (DAN-396), so schools stay explicit config.
    """
    configured = _json_env("FAMILY_SCHOOLS", None)
    if isinstance(configured, list) and configured:
        return [
            {"child": str(c.get("child", "Child")), "school": str(c.get("school", "")), "source": str(c.get("source", "dojo"))}
            for c in configured if isinstance(c, dict)
        ]
    roster = db.get_all_children() if hasattr(db, "get_all_children") else []
    return [{"child": _first(c.get("name") or c.get("first_name"), "Child"), "school": "", "source": "dojo"}
            for c in roster]


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

    connected = connected_sources()
    out = []
    for kid in kids:
        entry = {"child": kid["child"], "school": kid["school"], "date": target.isoformat(), "source": kid["source"]}
        if kid["source"] == "dojo" and dojo_summary is not None:
            entry.update(status="ok", **dojo_summary)
        elif kid["source"] in connected and kid["source"] != "dojo":
            # School email items: today's dated items, plus deadlines due today or tomorrow.
            soon = _email_items(db, kid["source"], target, target + timedelta(days=1))
            entry.update(
                status="ok",
                events=[{"date": i["date"], "title": i["title"], "type": i["type"]}
                        for i in soon if i["date"] == target.isoformat() and i["type"] != "deadline"],
                action_items=[{"summary": i["title"], "due": i["date"]} for i in soon if i["type"] == "deadline"],
            )
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

    connected = connected_sources()
    for source in sorted(connected - CONNECTED_SOURCES):
        items.extend(dict(i, source=source) for i in _email_items(db, source, first, last))
    items.sort(key=lambda e: (e.get("date") is None, e.get("date") or ""))
    not_connected = sorted({k["source"] for k in family_schools(db)} - connected)
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
    setup: Optional[Dict[str, Any]] = None,
    db: Any = None,
    schools: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Who has each pickup/drop-off, with each child's slot marked when their school is closed or
    dismisses early that day (DAN-436). ``db``/``schools`` enable the closure check."""
    result = _pickup_assignments(day, kind, calendars, fetch, children, setup)
    if db is not None and result.get("status") == "ok":
        apply_school_closures(result, db, schools if schools is not None else family_schools(db), fetch=fetch)
    return result


def _pickup_assignments(
    day: Optional[str],
    kind: str,
    calendars: Optional[Dict[str, str]],
    fetch: Callable[[str], str],
    children: Optional[List[str]],
    setup: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    import icalendar
    import recurring_ical_events

    target = _parse_day(day)
    reason = pickup_disabled_reason()
    if reason:
        return {"date": target.isoformat(), "status": "disabled", "reason": reason, "assignments": []}
    calendars = pickup_calendars() if calendars is None else calendars
    if not calendars:
        setup = family_setup() if setup is None else setup
        if setup and setup.get("blueprints"):
            return pickup_from_weekly_plan(setup, target, kind)
        return {"date": target.isoformat(), "status": "not_connected", "assignments": [],
                "reason": "No Family calendars (FAMILY_PICKUP_CALENDARS) or Family Schedule Helper plan available."}

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


WEEKLY_PLAN_NOTE = ("From the Family Schedule Helper's weekly plan and travel cover. One-day swaps, "
                    "cancellations and school holidays aren't visible here yet.")


def pickup_from_weekly_plan(setup: Dict[str, Any], target: date_cls, kind: str = "any") -> Dict[str, Any]:
    """Who has each pickup/drop-off on ``target`` per the helper's weekly plan (no locations or notes)."""
    caregivers = {c.get("id"): c for c in setup.get("caregivers") or [] if isinstance(c, dict)}
    kids = {c.get("id"): c for c in setup.get("children") or [] if isinstance(c, dict)}
    day_iso = target.isoformat()
    cover = {}
    for t in setup.get("travels") or []:
        if isinstance(t, dict) and str(t.get("startDate", "")) <= day_iso <= str(t.get("endDate", "")):
            cover[t.get("caregiverId")] = t.get("routeToCaregiverId")

    def name(cid: Any) -> str:
        return _first((caregivers.get(cid) or {}).get("name"), "Unassigned")

    assignments = []
    for bp in setup.get("blueprints") or []:
        if not isinstance(bp, dict):
            continue
        ev_kind = bp.get("category")
        if ev_kind not in ("pickup", "dropoff") or (kind != "any" and ev_kind != kind):
            continue
        if target.isoweekday() not in (bp.get("daysOfWeek") or []):
            continue
        kid = kids.get(bp.get("childId")) or {}
        if kid.get("isPet"):
            continue
        default = bp.get("defaultCaregiverId")
        owner = cover.get(default) or default
        entry = {"owner": name(owner), "kind": ev_kind, "start": bp.get("startTime") or None,
                 "end": bp.get("endTime") or None, "children": [_first(kid.get("name"), "Child")]}
        if owner != default:
            entry["covering_for"] = name(default)
        assignments.append(entry)

    assignments.sort(key=lambda a: (a["start"] or "", a["owner"]))
    result = {"date": day_iso, "status": "ok", "source": "weekly_plan", "assignments": assignments,
              "note": WEEKLY_PLAN_NOTE}
    if not assignments:
        result["note"] = "No pickup or drop-off in the weekly plan for this date. " + WEEKLY_PLAN_NOTE
    return result


# --- School closures (DAN-436): no school / early dismissal per child's school ---

CLOSURE_TYPES = ("day_off", "early_dismissal")


def school_calendars() -> Dict[str, str]:
    """Optional public school-calendar iCal feeds by source, e.g. {"dojo": url, "lehrman": url}."""
    cals = _json_env("FAMILY_SCHOOL_CALENDARS", {})
    return {str(k): str(v) for k, v in cals.items()} if isinstance(cals, dict) else {}


def school_closures(
    db: Any, day: date_cls, sources: List[str], fetch: Callable[[str], str] = _fetch_ics,
) -> Dict[str, Dict[str, str]]:
    """{source: {"type", "title"}} for each school source closed or dismissing early on ``day``.

    Sources: ClassDojo calendar events (``dojo``), stored school items for any source (school
    emails, or dates the parent confirmed) and the optional FAMILY_SCHOOL_CALENDARS feeds. A day off outranks an early
    dismissal. Any source that can't be read is skipped, never failing the pickup answer.
    """
    found: Dict[str, List[Dict[str, str]]] = {s: [] for s in sources}
    if "dojo" in found:
        found["dojo"] += [e for e in _dojo_events_between(db, day, day) if e["type"] in CLOSURE_TYPES]
    if hasattr(db, "get_school_items"):
        for src in found:
            try:
                rows = db.get_school_items(src, day.isoformat(), day.isoformat())
            except Exception as e:
                logger.warning(f"Could not read {src} school items: {type(e).__name__}")
                continue
            found[src] += [{"type": r["type"], "title": r["title"]} for r in rows if r.get("type") in CLOSURE_TYPES]
    for src, url in school_calendars().items():
        if src not in found:
            continue
        try:
            import icalendar
            import recurring_ical_events

            for ev in recurring_ical_events.of(icalendar.Calendar.from_ical(fetch(url))).at(day):
                title = str(ev.get("SUMMARY", "")).strip()
                kind = classify(title)
                if title and kind in CLOSURE_TYPES:
                    found[src].append({"type": kind, "title": title})
        except Exception as e:
            logger.warning(f"Could not read the {src} school calendar: {type(e).__name__}")
    out = {}
    for src, hits in found.items():
        if hits:
            best = min(hits, key=lambda h: CLOSURE_TYPES.index(h["type"]))
            out[src] = {"type": best["type"], "title": best["title"][:100]}
    return out


def apply_school_closures(
    result: Dict[str, Any], db: Any, schools: List[Dict[str, str]], fetch: Callable[[str], str] = _fetch_ics,
) -> Dict[str, Any]:
    """Mark each assignment whose child's school is closed (``no_school``) or dismisses early."""
    source_of = {k["child"].lower(): k["source"] for k in schools if k.get("child")}
    day = date_cls.fromisoformat(result["date"])
    closures = school_closures(db, day, sorted(set(source_of.values())), fetch=fetch)
    if not closures:
        return result
    for a in result.get("assignments", []):
        hits = [closures[source_of[c.lower()]] for c in a.get("children", []) if source_of.get(c.lower()) in closures]
        if not hits:
            continue
        hit = min(hits, key=lambda h: CLOSURE_TYPES.index(h["type"]))
        if hit["type"] == "day_off":
            a["status"] = "no_school"
        a["school_note"] = hit["title"]
    result["school_closures"] = [{"source": s, **c} for s, c in sorted(closures.items())]
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
