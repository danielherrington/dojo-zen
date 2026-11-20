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
