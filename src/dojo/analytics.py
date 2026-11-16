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

logger = logging.getLogger(__name__)

EMAIL_ID_RE = re.compile(r"^[0-9a-f]{32}$")
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


def new_email_id() -> str:
    return uuid.uuid4().hex


def is_email_id(value: Optional[str]) -> bool:
    return bool(value) and bool(EMAIL_ID_RE.match(value))


def client_category(user_agent: Optional[str]) -> str:
    """Coarse client bucket only; the raw user-agent is never stored."""
    return "gmail_proxy" if user_agent and "GoogleImageProxy" in user_agent else "other"


# --- Email instrumentation ---

_HREF_RE = re.compile(r'href="([^"]*)"')


def instrument_html(html: str, email_id: str, base_url: str) -> str:
    """Rewrite portal links to the click tracker and append the open pixel."""
    base = base_url.rstrip("/")
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
        tracked = f"{base}/t/c/{email_id}?u={quote(path, safe='')}"
        return f'href="{html_lib.escape(tracked, quote=True)}"'

    instrumented = _HREF_RE.sub(_rewrite, html)
    pixel = (
        f'<img src="{base}/t/o/{email_id}.gif" width="1" height="1" alt="" '
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
            if status["code"] < 400 and self.recorder is not None:
                self._record(family_now().date().isoformat(), channel)

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
    for row in usage:
        day = row.get("day")
        if not day or day < since_day:
            continue
        for channel, count in row.items():
            if channel == "day" or not isinstance(count, (int, float)):
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
        "usage": {
            "totals": dict(totals),
            "daily": [{"day": d, **daily[d]} for d in sorted(daily)],
            "busiest_weekday": _busiest_weekday(daily),
        },
        "caveats": CAVEATS,
    }


def _busiest_weekday(daily: Dict[str, Dict[str, int]]) -> Optional[str]:
    by_weekday: Counter = Counter()
    for day, counts in daily.items():
        try:
            by_weekday[datetime.fromisoformat(day).strftime("%A")] += sum(counts.values())
        except ValueError:
            continue
    return by_weekday.most_common(1)[0][0] if by_weekday else None
