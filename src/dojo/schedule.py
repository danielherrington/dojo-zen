"""Family-local time helpers and the briefing email send policy.

Cloud Run containers run in UTC, so anything that depends on the family's
wall clock (which day it is, morning vs. evening) must convert explicitly
to the configured timezone rather than relying on the system clock.

Send policy (DAN-98):
  * Monday–Friday: send the morning briefing.
  * Saturday: never send a briefing (urgent alerts are unaffected).
  * Sunday: only send in the evening, as a preview of the school week.
"""

from datetime import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from dojo.config import settings

# Sunday sends are allowed from this local hour onward (the evening preview).
SUNDAY_EVENING_START_HOUR = 12

SATURDAY = 5
SUNDAY = 6


def family_timezone() -> ZoneInfo:
    """Return the family's configured timezone (defaults to America/New_York)."""
    return ZoneInfo(settings.digest_timezone)


def local_now() -> datetime:
    """Current time in the family's timezone, regardless of the server's TZ."""
    return datetime.now(family_timezone())


def digest_schedule_decision(now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Decide whether a scheduled briefing should be sent at ``now``.

    Returns ``(should_send, reason)``. Naive datetimes are treated as already
    being in the family's timezone; aware ones are converted to it.
    """
    if now is None:
        now = local_now()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=family_timezone())
    else:
        now = now.astimezone(family_timezone())

    weekday = now.weekday()
    if weekday == SATURDAY:
        return False, "No briefing on Saturdays"
    if weekday == SUNDAY and now.hour < SUNDAY_EVENING_START_HOUR:
        return False, "Sunday briefing is sent in the evening only"
    return True, "Scheduled send window"
