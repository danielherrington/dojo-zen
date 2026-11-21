"""herrington.usage.v1 product-analytics events (DAN-233).

See linear-conductor/docs/analytics-standard.md. Each event is one JSON line on stdout; on Cloud Run that
becomes a Cloud Logging entry with ``jsonPayload.schema == "herrington.usage.v1"``, which a log sink can
route to BigQuery (90-day partition expiry).

Privacy (family data):
* ``actor`` is a salted HMAC of the presented access token, never a raw id, email, IP or name. With no
  ``ANALYTICS_SALT`` the field is omitted. Email events never carry an actor: one email goes to the whole
  family, so there is no individual to attribute.
* ``props`` hold small typed values only (counts, enum-like labels, random email ids). No message bodies,
  subjects, sender names or other free text.
* ``ANALYTICS=off`` turns off every event, counter and tracker.
"""

import hashlib
import hmac
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

SCHEMA = "herrington.usage.v1"
APP = "dojo-zen"

TRIAGE_PREFIX = "triage__"
GUARD_PREFIX = "guard__"
PREVENTED_ALERT = "prevented_alert"


def _settings():
    from dojo.config import settings
    return settings


def _app_version() -> str:
    try:
        from importlib.metadata import version
        return version("dojo")
    except Exception:
        return "unknown"


APP_VERSION = _app_version()


def actor_hash(raw: Optional[str], salt: Optional[str] = None) -> Optional[str]:
    """Salted, truncated HMAC-SHA256 of an identifier. None without a salt or identifier."""
    salt = _settings().analytics_salt if salt is None else salt
    if not raw or not salt:
        return None
    return hmac.new(salt.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]


def mcp_client_family(user_agent: Optional[str]) -> str:
    ua = (user_agent or "").lower()
    for family in ("claude", "cursor", "chatgpt", "openai", "vscode", "windsurf"):
        if family in ua:
            return "chatgpt" if family == "openai" else family
    return "other"


def v1_channel(usage_channel: str, user_agent: Optional[str] = None) -> str:
    """Map a DAN-200 usage channel onto the standard's channel names."""
    if usage_channel == "mcp":
        return f"mcp:{mcp_client_family(user_agent)}"
    if usage_channel == "sms":
        return "sms"
    return "web"


def emit(
    event: str,
    channel: str,
    *,
    feature: Optional[str] = None,
    funnel: Optional[str] = None,
    step: Optional[str] = None,
    actor: Optional[str] = None,
    props: Optional[Dict[str, Any]] = None,
    stream=None,
) -> Optional[Dict[str, Any]]:
    """Write one v1 event line. Never raises; returns the event (or None when analytics is off)."""
    s = _settings()
    if not s.analytics_enabled:
        return None
    record: Dict[str, Any] = {
        "schema": SCHEMA,
        "app": APP,
        "env": s.analytics_env,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "channel": channel,
        "app_version": APP_VERSION,
        # Cloud Logging reads "severity" from structured lines; keeps these out of error views.
        "severity": "INFO",
    }
    for key, value in (("feature", feature), ("funnel", funnel), ("step", step), ("actor", actor)):
        if value:
            record[key] = value
    if props:
        record["props"] = props
    try:
        out = stream or sys.stdout
        out.write(json.dumps(record, separators=(",", ":")) + "\n")
        out.flush()
    except Exception as e:
        logger.warning(f"Could not emit usage event: {e}")
    return record


# --- Triage (which alert categories fire, and false-alarm guard corrections) ---

def triage_counter_names(category: str, guards: Iterable[str], alerted: bool) -> list:
    """Daily aggregate counter fields for one triage decision (stored on dojo_usage, 400-day TTL)."""
    names = [f"{TRIAGE_PREFIX}{category}"]
    guards = list(dict.fromkeys(guards))
    names += [f"{GUARD_PREFIX}{g}" for g in guards]
    if guards and not alerted:
        # A keyword matched but every match was suppressed by a guard: an alert we didn't send.
        names.append(f"{GUARD_PREFIX}{PREVENTED_ALERT}")
    return names


def record_triage(db: Any, decision: Any, day: Optional[str] = None) -> None:
    """Count a first-time triage decision and emit a ``triage`` event. Never raises."""
    s = _settings()
    if not s.analytics_enabled:
        return
    from dojo.alert_evaluator import UrgencyLevel
    from dojo.tz import family_now

    category = getattr(decision, "category", None) or "unknown"
    guards = list(getattr(decision, "guards", []) or [])
    alerted = decision.urgency == UrgencyLevel.IMMEDIATE
    day = day or family_now().date().isoformat()
    if db is not None and hasattr(db, "increment_usage"):
        for name in triage_counter_names(category, guards, alerted):
            try:
                db.increment_usage(day, name)
            except Exception as e:
                logger.warning(f"Triage counter write failed: {e}")
                break
    emit("triage", "cron", feature="alert_triage", props={
        "category": category,
        "urgency": decision.urgency.value,
        "item_type": decision.item_type,
        "guards": guards,
    })


# --- Calls, errors, zero results and durations (DAN-284) ---
# Daily counters on the existing dojo_usage/{day} docs (400-day TTL), with prefixed field names so the
# report can split them from channel totals. Per-event lines carry only names, codes, counts and ms.

TOOL_PREFIX = "tool__"      # per-feature calls: tool__search_feed, tool__ask
ERROR_PREFIX = "error__"    # error__<feature>__<code>
ZERO_PREFIX = "zero__"      # zero__<feature>: calls that found nothing
ZQ_PREFIX = "zq__"          # zq__<token>: normalised tokens from zero-result queries (shown only at count >= 3)
DUR_PREFIX = "dur__"        # dur__<feature>__<bucket upper bound ms | inf>
DURATION_BUCKETS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)
ZERO_QUERY_MIN_COUNT = 3

_STOPWORDS = frozenset("""
a about after all also am an and any are as at be been before but by can could did do does for from get got
had has have how i if in into is it its just me my need needs next no not of on or our out over please so
some than that the their them then there these they this to today tomorrow up us was we week were what
when where which who why will with would you your yet any anything else does there
""".split())
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def duration_bucket(ms: float) -> str:
    for upper in DURATION_BUCKETS:
        if ms <= upper:
            return str(upper)
    return "inf"


def _safe_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", value.lower())[:48] or "unknown"


def query_tokens(text: Optional[str], names: Iterable[str] = ()) -> list:
    """Normalised, privacy-filtered tokens from a query that found nothing.

    Drops stopwords, short or long words, anything with digits or '@', words capitalised mid-sentence
    (likely names) and the given known names. At most 6 tokens.
    """
    text = re.sub(r"\S+@\S+", " ", text or "")
    blocked = {n.lower() for name in names for n in str(name).split() if n}
    out = []
    for i, m in enumerate(_WORD.finditer(re.sub(r"\S*\d\S*", " ", text))):
        word = m.group(0).strip("'-")
        low = word.lower()
        if i > 0 and word[:1].isupper():
            continue
        if len(low) < 3 or len(low) > 20 or low in _STOPWORDS or low in blocked or low in out:
            continue
        out.append(low)
        if len(out) == 6:
            break
    return out


def result_count(result: Any) -> Optional[int]:
    """How many things a tool returned, or None if that isn't meaningful."""
    if result is None:
        return 0
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if isinstance(result, (list, tuple)):
        return len(result)
    if isinstance(result, dict):
        for key in ("items", "assignments", "results", "sources"):
            if isinstance(result.get(key), list):
                return len(result[key])
        return None
    if isinstance(result, str):
        return 1 if result.strip() else 0
    return None


def call_counter_names(feature: str, duration_ms: float, error_code: Optional[str] = None,
                       zero: bool = False, tokens: Iterable[str] = ()) -> list:
    f = _safe_key(feature)
    names = [f"{TOOL_PREFIX}{f}", f"{DUR_PREFIX}{f}__{duration_bucket(duration_ms)}"]
    if error_code:
        names.append(f"{ERROR_PREFIX}{f}__{_safe_key(error_code)}")
    elif zero:
        names.append(f"{ZERO_PREFIX}{f}")
        names += [f"{ZQ_PREFIX}{_safe_key(t)}" for t in tokens]
    return names


def _bump(db: Any, names: Iterable[str], day: Optional[str] = None, background: bool = True) -> None:
    if db is None or not hasattr(db, "increment_usage"):
        return
    from dojo.tz import family_now

    day = day or family_now().date().isoformat()
    names = list(names)

    def _do() -> None:
        for name in names:
            try:
                db.increment_usage(day, name)
            except Exception as e:
                logger.warning(f"Usage counter write failed: {type(e).__name__}")
                return

    if background:
        import threading
        threading.Thread(target=_do, daemon=True).start()
    else:
        _do()


def record_call(
    db: Any,
    feature: str,
    channel: str,
    duration_ms: float,
    *,
    result: Any = None,
    error_code: Optional[str] = None,
    query: Optional[str] = None,
    names: Iterable[str] = (),
    zero: Optional[bool] = None,
    background: bool = True,
) -> None:
    """Count one call of a feature (tool, endpoint, cron job) and emit its events. Never raises.

    ``query`` is used only for zero-result token counters (filtered by ``query_tokens``); it is never
    put in an event line.
    """
    try:
        if not _settings().analytics_enabled:
            return
        count = None if error_code else result_count(result)
        if zero is None:
            zero = count == 0
        tokens = query_tokens(query, names) if (zero and query and not error_code) else []
        _bump(db, call_counter_names(feature, duration_ms, error_code, zero, tokens), background=background)
        props: Dict[str, Any] = {"duration_ms": int(duration_ms)}
        if count is not None:
            props["result_count"] = count
        if error_code:
            emit("error", channel, feature=feature, props={**props, "code": _safe_key(error_code)})
        else:
            emit("feature_used", channel, feature=feature, props=props)
            if zero:
                emit("zero_result", channel, feature=feature, props={"duration_ms": int(duration_ms)})
    except Exception as e:  # analytics must never break the feature
        logger.warning(f"record_call failed: {type(e).__name__}")


def record_error(db: Any, feature: str, channel: str, error: Any, background: bool = True) -> None:
    """Count a failure by exception class name (never its message text)."""
    try:
        if not _settings().analytics_enabled:
            return
        code = _safe_key(error if isinstance(error, str) else type(error).__name__)
        _bump(db, [f"{ERROR_PREFIX}{_safe_key(feature)}__{code}"], background=background)
        emit("error", channel, feature=feature, props={"code": code})
    except Exception as e:
        logger.warning(f"record_error failed: {type(e).__name__}")
