"""Email open/click analytics and usage-pattern counters (DAN-200).

Three pieces:

* **Email instrumentation** – every briefing/alert gets a random ``email_id``. Links into the portal are
  rewritten to ``/t/c/{email_id}?u=<path>`` and a 1x1 pixel ``/t/o/{email_id}.gif`` is appended. The
  tracking endpoints live in :mod:`dojo.server`; they record an event and never expose data.
* **Usage counters** – :class:`UsageMiddleware` bumps one daily counter per channel (portal view, API,
  Ask, MCP, SMS). No IPs, user-agents or query strings are stored.
* **Summary** – :func:`summarize` turns the raw sends/events/counters into open rates, click rates,
  time-to-open, opens by hour (family timezone) and daily usage.

Everything here is best-effort: a failure to record analytics must never block an email or a request.
"""

import asyncio
import html as html_lib
import logging
import re
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.parse import quote, urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dojo.tz import family_now, family_tz, to_family_tz
from dojo.usage_v1 import (
    DENIED_PREFIX,
    DUR_PREFIX,
    DURATION_BUCKETS,
    ERROR_PREFIX,
    GUARD_PREFIX,
    PREVENTED_ALERT,
    TOOL_PREFIX,
    TRIAGE_PREFIX,
    ZERO_PREFIX,
    ZERO_QUERY_MIN_COUNT,
    ZQ_PREFIX,
)

logger = logging.getLogger(__name__)

EMAIL_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Core journeys (DAN-233). Steps in order; "opened" includes clicks (images may be blocked).
FUNNELS = {
    "alert": ("sent", "opened", "action_taken"),   # action_taken = clicked through to the flagged item
    "digest": ("sent", "opened", "link_clicked"),
}


def funnel_click_step(kind: Optional[str], target: Optional[str]) -> str:
    return "action_taken" if kind == "alert" and target and "item=" in target else "link_clicked"
EVENT_OPEN = "open"
EVENT_CLICK = "click"

# 1x1 transparent GIF
PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

CAVEATS = [
    "Apple Mail Privacy Protection pre-loads images, which inflates opens.",
    "Gmail proxies and caches images, so repeat opens are under-counted.",
    "Each email goes to all family recipients at once: an open means someone opened it, not who.",
    "Email security scanners can pre-click links.",
]


# --- Retention (DAN-205) ---
# Firestore deletes documents whose ``expireAt`` timestamp has passed once a TTL policy is enabled on
# the collection (usually within 24h). SQLite has no TTL, so DojoDatabase purges on read/write instead.
EXPIRE_FIELD = "expireAt"


def event_retention_days() -> int:
    from dojo.config import settings
    return settings.analytics_retention_days


def usage_retention_days() -> int:
    from dojo.config import settings
    return settings.usage_retention_days


def expire_at(start: Any, days: int) -> Optional[datetime]:
    """Expiry instant ``days`` after ``start`` (a datetime, ISO timestamp or YYYY-MM-DD day)."""
    dt = start if isinstance(start, datetime) else _parse(start)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt + timedelta(days=days)


def retention_cutoffs(now: Optional[datetime] = None) -> Dict[str, str]:
    """ISO cutoffs: rows older than these are past retention."""
    now = now or datetime.now(timezone.utc)
    return {
        "events_before": (now - timedelta(days=event_retention_days())).isoformat(),
        "usage_before_day": (now - timedelta(days=usage_retention_days())).date().isoformat(),
    }


def new_email_id() -> str:
    return uuid.uuid4().hex


def is_email_id(value: Optional[str]) -> bool:
    return bool(value) and bool(EMAIL_ID_RE.match(value))


def client_category(user_agent: Optional[str]) -> str:
    """Coarse client bucket only; the raw user-agent is never stored."""
    return "gmail_proxy" if user_agent and "GoogleImageProxy" in user_agent else "other"


# --- Email instrumentation ---

_HREF_RE = re.compile(r'href="([^"]*)"')


def instrument_html(html: str, email_id: str, base_url: str, kind: Optional[str] = None) -> str:
    """Rewrite portal links to the click tracker and append the open pixel.

    ``kind`` (digest/alert) rides along as ``k=`` so open/click events can be attributed to a funnel
    without a database read.
    """
    base = base_url.rstrip("/")
    k = f"&k={kind}" if kind in FUNNELS else ""
    split_base = urlsplit(base)

    def _rewrite(match: "re.Match[str]") -> str:
        href = html_lib.unescape(match.group(1))
        target = urlsplit(href)
        if (target.scheme, target.netloc) != (split_base.scheme, split_base.netloc):
            return match.group(0)
        path = target.path or "/"
        if target.query:
            path += f"?{target.query}"
        if target.fragment:
            path += f"#{target.fragment}"
        tracked = f"{base}/t/c/{email_id}?u={quote(path, safe='')}{k}"
        return f'href="{html_lib.escape(tracked, quote=True)}"'

    instrumented = _HREF_RE.sub(_rewrite, html)
    pixel = (
        f'<img src="{base}/t/o/{email_id}.gif{"?k=" + kind if k else ""}" width="1" height="1" alt="" '
        f'style="display:block;width:1px;height:1px;border:0;" />'
    )
    if "</body>" in instrumented:
        return instrumented.replace("</body>", f"{pixel}</body>", 1)
    return instrumented + pixel


def record_send_safely(
    db: Any, email_id: str, kind: str, subject: str, recipient_count: int, item_count: int = 0
) -> None:
    if db is None or not hasattr(db, "record_email_send"):
        return
    try:
        db.record_email_send(
            email_id=email_id,
            kind=kind,
            subject=subject,
            recipient_count=recipient_count,
            item_count=item_count,
            sent_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:  # analytics must never block a send
        logger.warning(f"Could not record email send for analytics: {e}")
    from dojo.usage_v1 import emit
    emit("funnel_step", "email", funnel=kind, step="sent",
         props={"email_id": email_id, "recipient_count": recipient_count, "item_count": item_count})


# --- Usage counters ---

def usage_channel(method: str, path: str) -> Optional[str]:
    """Map a request to a usage channel, or None if it isn't counted."""
    if path == "/" and method == "GET":
        return "portal_view"
    if path == "/mcp" or path.startswith("/mcp/"):
        return "mcp" if method == "POST" else None
    if path == "/api/ask":
        return "ask"
    if path == "/api/sms/webhook":
        return "sms"
    if path.startswith("/api/") and not path.startswith(("/api/cron/", "/api/analytics")) and path != "/api/status":
        return "api"
    return None


class UsageMiddleware:
    """Pure ASGI middleware that counts requests per channel per family-timezone day.

    The counter write runs in a worker thread after the response, so it adds no latency and its failures
    are only logged.
    """

    def __init__(self, app: ASGIApp, recorder: Optional[Callable[[str, str], None]] = None,
                 enabled: Optional[Callable[[], bool]] = None, run_inline: bool = False):
        self.app = app
        self.recorder = recorder
        self.enabled = enabled or (lambda: True)
        self.run_inline = run_inline

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled():
            await self.app(scope, receive, send)
            return

        channel = usage_channel(scope.get("method", "GET"), scope.get("path", ""))
        if channel is None:
            await self.app(scope, receive, send)
            return

        status = {"code": 500}

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            if status["code"] < 400:
                if self.recorder is not None:
                    self._record(family_now().date().isoformat(), channel)
                _emit_feature_used(scope, channel)
            elif status["code"] in (401, 403):
                # A locked-out client (e.g. after a code rotation) must be visible (DAN-526).
                if self.recorder is not None:
                    self._record(family_now().date().isoformat(), f"{DENIED_PREFIX}{channel}")
                _emit_refused(scope, channel, status["code"])

    def _record(self, day: str, channel: str) -> None:
        def _do() -> None:
            try:
                self.recorder(day, channel)
            except Exception as e:
                logger.warning(f"Usage counter write failed: {e}")

        if self.run_inline:
            _do()
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, _do)
        except RuntimeError:
            _do()


def _emit_feature_used(scope: Scope, channel: str) -> None:
    from dojo.auth import SESSION_COOKIE, _bearer, _cookie
    from dojo.usage_v1 import actor_hash, emit, v1_channel

    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    token = _bearer(headers.get("authorization")) or _cookie(headers.get("cookie"), SESSION_COOKIE)
    emit("feature_used", v1_channel(channel, headers.get("user-agent")), feature=channel, actor=actor_hash(token))


def _emit_refused(scope: Scope, channel: str, code: int) -> None:
    """v1 error event for a refused request: client family only, never the presented token."""
    from dojo.usage_v1 import emit, v1_channel

    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    emit("error", v1_channel(channel, headers.get("user-agent")), feature=channel,
         props={"code": "auth_refused", "status": code})


# --- Summary ---

def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _rate(n: int, d: int) -> Optional[float]:
    return round(n / d, 3) if d else None


def _target_label(target: Optional[str]) -> str:
    if not target:
        return "unknown"
    return "portal_item" if "item=" in target else ("portal_home" if target.split("#")[0] in ("/", "") else target.split("?")[0])


def summarize(
    sends: Iterable[Dict[str, Any]],
    events: Iterable[Dict[str, Any]],
    usage: Iterable[Dict[str, Any]],
    days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compute email and usage analytics from raw rows (storage-agnostic, so SQLite and Firestore agree)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    tz = family_tz()

    sends_by_id = {}
    for s in sends:
        sent_at = _parse(s.get("sent_at"))
        if sent_at and sent_at >= since:
            sends_by_id[s["email_id"]] = {**s, "_sent_at": sent_at}

    first_open: Dict[str, datetime] = {}
    clicked: set = set()
    item_clicked: set = set()
    clicks_by_kind: Counter = Counter()
    targets: Counter = Counter()
    opens_by_hour: Counter = Counter()
    opens_by_client: Counter = Counter()
    for e in events:
        sent = sends_by_id.get(e.get("email_id"))
        at = _parse(e.get("created_at"))
        if not sent or not at:
            continue
        if e.get("event_type") == EVENT_OPEN:
            opens_by_hour[to_family_tz(at).hour] += 1
            opens_by_client[e.get("client") or "other"] += 1
            if e["email_id"] not in first_open or at < first_open[e["email_id"]]:
                first_open[e["email_id"]] = at
        elif e.get("event_type") == EVENT_CLICK:
            clicked.add(e["email_id"])
            if "item=" in (e.get("target") or ""):
                item_clicked.add(e["email_id"])
            clicks_by_kind[sent.get("kind", "other")] += 1
            targets[_target_label(e.get("target"))] += 1

    # A click implies the email was opened even if images were blocked.
    opened = set(first_open) | clicked

    by_kind: Dict[str, Dict[str, Any]] = {}
    for kind in sorted({s.get("kind", "other") for s in sends_by_id.values()}):
        ids = [i for i, s in sends_by_id.items() if s.get("kind", "other") == kind]
        n_open = sum(1 for i in ids if i in opened)
        n_click = sum(1 for i in ids if i in clicked)
        minutes = [
            (first_open[i] - sends_by_id[i]["_sent_at"]).total_seconds() / 60
            for i in ids if i in first_open and first_open[i] >= sends_by_id[i]["_sent_at"]
        ]
        by_kind[kind] = {
            "sent": len(ids),
            "opened": n_open,
            "open_rate": _rate(n_open, len(ids)),
            "clicked": n_click,
            "click_rate": _rate(n_click, len(ids)),
            "click_to_open_rate": _rate(n_click, n_open),
            "total_clicks": clicks_by_kind[kind],
            "median_minutes_to_open": round(statistics.median(minutes), 1) if minutes else None,
        }

    total = len(sends_by_id)
    since_day = to_family_tz(since).date().isoformat()
    daily: Dict[str, Dict[str, int]] = defaultdict(dict)
    totals: Counter = Counter()
    triage_categories: Counter = Counter()
    guard_counts: Counter = Counter()
    calls: Counter = Counter()
    errors: Counter = Counter()
    zeros: Counter = Counter()
    zero_tokens: Counter = Counter()
    durations: Dict[str, Counter] = defaultdict(Counter)
    refused: Counter = Counter()
    refused_daily: Dict[str, Counter] = defaultdict(Counter)
    prefixed = ((TOOL_PREFIX, calls), (ERROR_PREFIX, errors), (ZERO_PREFIX, zeros), (ZQ_PREFIX, zero_tokens))
    for row in usage:
        day = row.get("day")
        if not day or day < since_day:
            continue
        for channel, count in row.items():
            if channel == "day" or not isinstance(count, (int, float)):
                continue
            if channel.startswith(TRIAGE_PREFIX):
                triage_categories[channel[len(TRIAGE_PREFIX):]] += int(count)
                continue
            if channel.startswith(GUARD_PREFIX):
                guard_counts[channel[len(GUARD_PREFIX):]] += int(count)
                continue
            if channel.startswith(DENIED_PREFIX):
                ch = channel[len(DENIED_PREFIX):]
                refused[ch] += int(count)
                refused_daily[day][ch] += int(count)
                continue
            if channel.startswith(DUR_PREFIX):
                feature, _, bucket = channel[len(DUR_PREFIX):].rpartition("__")
                durations[feature][bucket] += int(count)
                continue
            hit = next((c for p, c in prefixed if channel.startswith(p)), None)
            if hit is not None:
                hit[channel.split("__", 1)[1]] += int(count)
                continue
            daily[day][channel] = daily[day].get(channel, 0) + int(count)
            totals[channel] += int(count)

    return {
        "window_days": days,
        "since": since.isoformat(),
        "timezone": str(tz),
        "email": {
            "sent": total,
            "opened": sum(1 for i in sends_by_id if i in opened),
            "open_rate": _rate(sum(1 for i in sends_by_id if i in opened), total),
            "clicked": len(clicked & set(sends_by_id)),
            "click_rate": _rate(len(clicked & set(sends_by_id)), total),
            "by_kind": by_kind,
            "opens_by_hour": {str(h): opens_by_hour[h] for h in sorted(opens_by_hour)},
            "opens_by_client": dict(opens_by_client),
            "top_click_targets": dict(targets.most_common(10)),
        },
        "funnels": _funnels(sends_by_id, opened, item_clicked, clicked),
        "triage": {
            "categories": dict(triage_categories.most_common()),
            "false_alarm_guards": {k: v for k, v in guard_counts.most_common() if k != PREVENTED_ALERT},
            "alerts_prevented_by_guards": guard_counts.get(PREVENTED_ALERT, 0),
        },
        "features": {
            "calls": dict(calls.most_common()),
            "errors": {k.replace("__", ": ", 1): v for k, v in errors.most_common()},
            "zero_results": {f: {"count": n, "rate": _rate(n, calls.get(f, 0))} for f, n in zeros.most_common()},
            "zero_result_terms": {t: n for t, n in zero_tokens.most_common() if n >= ZERO_QUERY_MIN_COUNT},
            "duration_ms": {f: _percentiles(b) for f, b in sorted(durations.items())},
        },
        "refused": {
            "totals": dict(refused.most_common()),
            # Days where a channel was refused more often than it succeeded: likely a locked-out client.
            "lockout_days": [
                {"day": d, "channel": ch, "refused": n, "ok": daily.get(d, {}).get(ch, 0)}
                for d in sorted(refused_daily) for ch, n in sorted(refused_daily[d].items())
                if n > daily.get(d, {}).get(ch, 0)
            ],
        },
        "usage": {
            "totals": dict(totals),
            "daily": [{"day": d, **daily[d]} for d in sorted(daily)],
            "busiest_weekday": _busiest_weekday(daily),
        },
        "retention": {
            "email_events_days": event_retention_days(),
            "usage_counters_days": usage_retention_days(),
        },
        "caveats": CAVEATS,
    }


def _percentiles(buckets: Counter) -> Dict[str, Any]:
    """Approximate p50/p95 (bucket upper bounds, in ms) from duration bucket counts."""
    order = [str(b) for b in DURATION_BUCKETS] + ["inf"]
    total = sum(buckets.values())
    out: Dict[str, Any] = {"calls": total}
    for name, q in (("p50", 0.5), ("p95", 0.95)):
        seen = 0
        for b in order:
            seen += buckets.get(b, 0)
            if total and seen >= q * total:
                out[name] = f"<={b}" if b != "inf" else f">{DURATION_BUCKETS[-1]}"
                break
    return out


def _funnels(sends_by_id: Dict[str, Dict[str, Any]], opened: set, item_clicked: set, clicked: set) -> Dict[str, Any]:
    """Step counts and conversion for each core-journey funnel, from the 90-day email rows."""
    out: Dict[str, Any] = {}
    for kind, steps in FUNNELS.items():
        ids = {i for i, s in sends_by_id.items() if s.get("kind") == kind}
        last_step = item_clicked if kind == "alert" else clicked
        counts = [len(ids), len(ids & opened), len(ids & last_step)]
        out[kind] = [
            {
                "step": step,
                "count": n,
                "from_previous": _rate(n, counts[i - 1]) if i else None,
                "from_start": _rate(n, counts[0]) if i else None,
            }
            for i, (step, n) in enumerate(zip(steps, counts))
        ]
    return out


def _busiest_weekday(daily: Dict[str, Dict[str, int]]) -> Optional[str]:
    by_weekday: Counter = Counter()
    for day, counts in daily.items():
        try:
            by_weekday[datetime.fromisoformat(day).strftime("%A")] += sum(counts.values())
        except ValueError:
            continue
    return by_weekday.most_common(1)[0][0] if by_weekday else None


# --- Analyst report (DAN-233): `dojo analytics report [--days 7] [--json]` ---

REPORT_GAPS = [
    "Active users / D1-D7-D30 retention: actors exist only in herrington.usage.v1 log events "
    "(BigQuery sink not set up yet), not in Firestore aggregates.",
    "Durations are bucketed (50 ms ... 30 s), so p50/p95 are upper bounds, not exact values.",
    "Zero-result terms are shown only at 3+ uses, with likely names, emails and numbers dropped.",
]


def build_report(db: Any, days: int = 7, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    rows = db.get_analytics_rows(since.isoformat(), to_family_tz(since).date().isoformat())
    report = summarize(rows["sends"], rows["events"], rows["usage"], days=days, now=now)
    report["gaps"] = REPORT_GAPS
    return report


def _pct(x: Optional[float]) -> str:
    return "  —  " if x is None else f"{x * 100:5.1f}%"


def format_report(r: Dict[str, Any]) -> str:
    lines = [f"DojoZen analytics: last {r['window_days']} days (since {r['since'][:10]}, {r['timezone']})", ""]

    lines.append("Daily activity by channel")
    channels = sorted(r["usage"]["totals"])
    if r["usage"]["daily"]:
        lines.append("  " + "day".ljust(12) + "".join(c.rjust(13) for c in channels))
        for row in r["usage"]["daily"]:
            lines.append("  " + row["day"].ljust(12) + "".join(str(row.get(c, 0)).rjust(13) for c in channels))
    else:
        lines.append("  (no activity recorded)")
    lines.append("")

    lines.append("Funnels (step count, conversion from previous step / from start)")
    for name, steps in r["funnels"].items():
        lines.append(f"  {name}")
        for s in steps:
            lines.append(f"    {s['step'].ljust(14)}{str(s['count']).rjust(5)}   {_pct(s['from_previous'])} / {_pct(s['from_start'])}")
    lines.append("")

    lines.append("Features by use (top to bottom)")
    totals = sorted(r["usage"]["totals"].items(), key=lambda kv: -kv[1])
    lines += [f"  {k.ljust(14)}{str(v).rjust(6)}" for k, v in totals] or ["  (none)"]
    lines.append("")

    f = r.get("features", {})
    lines.append("Features by call (MCP tools, Ask, cron jobs)")
    lines += [f"  {k.ljust(24)}{str(v).rjust(6)}" for k, v in f.get("calls", {}).items()] or ["  (none)"]
    lines.append("Durations (ms, bucket upper bound)")
    lines += [f"  {k.ljust(24)}p50 {str(v.get('p50', '-')).rjust(8)}   p95 {str(v.get('p95', '-')).rjust(8)}"
              for k, v in f.get("duration_ms", {}).items()] or ["  (none)"]
    lines.append("Top errors (feature: code)")
    lines += [f"  {k.ljust(40)}{str(v).rjust(6)}" for k, v in f.get("errors", {}).items()] or ["  (none)"]
    lines.append("Zero results (count, share of calls)")
    lines += [f"  {k.ljust(24)}{str(v['count']).rjust(6)}   {_pct(v['rate'])}"
              for k, v in f.get("zero_results", {}).items()] or ["  (none)"]
    lines.append(f"Zero-result terms (count >= {ZERO_QUERY_MIN_COUNT})")
    lines += [f"  {k.ljust(24)}{str(v).rjust(6)}" for k, v in f.get("zero_result_terms", {}).items()] or ["  (none)"]
    lines.append("")

    rf = r.get("refused", {})
    lines.append("Refused requests (401/403) by channel")
    lines += [f"  {k.ljust(24)}{str(v).rjust(6)}" for k, v in rf.get("totals", {}).items()] or ["  (none)"]
    for lo in rf.get("lockout_days", []):
        lines.append(f"  ! {lo['day']} {lo['channel']}: {lo['refused']} refused vs {lo['ok']} ok (a client may be locked out)")
    lines.append("")

    t = r["triage"]
    lines.append("Alert triage: categories fired")
    lines += [f"  {k.ljust(16)}{str(v).rjust(6)}" for k, v in t["categories"].items()] or ["  (none)"]
    lines.append("False-alarm guards (keyword matches suppressed)")
    lines += [f"  {k.ljust(16)}{str(v).rjust(6)}" for k, v in t["false_alarm_guards"].items()] or ["  (none)"]
    lines.append(f"  alerts prevented by guards: {t['alerts_prevented_by_guards']}")
    lines.append("")

    lines.append("Not measured yet")
    lines += [f"  - {g}" for g in r.get("gaps", [])]
    lines.append("Caveats")
    lines += [f"  - {c}" for c in r["caveats"]]
    return "\n".join(lines)
