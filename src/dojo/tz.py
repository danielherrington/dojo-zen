"""Family wall-clock helpers (DAN-165).

Cloud Run containers run in UTC, so anything that depends on the family's calendar
(which day is "Today", Morning vs. Evening, the Sunday-evening week-ahead window) must
convert explicitly to ``DIGEST_TIMEZONE`` rather than rely on the server's local time.

Salvaged from the timezone half of commit e5e3879 (branch claude/brave-euler-qjljb0, DAN-98).
"""

import logging
from datetime import datetime, timezone, tzinfo
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_FAMILY_TIMEZONE = "America/New_York"


@lru_cache(maxsize=8)
def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.error("Unknown DIGEST_TIMEZONE %r; falling back to %s", name, DEFAULT_FAMILY_TIMEZONE)
        return ZoneInfo(DEFAULT_FAMILY_TIMEZONE)


def family_tz() -> ZoneInfo:
    """The family's configured IANA timezone (``DIGEST_TIMEZONE``, default America/New_York)."""
    from dojo.config import settings
    return _zone(settings.digest_timezone or DEFAULT_FAMILY_TIMEZONE)


def family_now() -> datetime:
    """Current time on the family's wall clock, independent of the server's TZ."""
    return datetime.now(family_tz())


def to_family_tz(dt: Optional[datetime], naive_tz: Optional[tzinfo] = timezone.utc) -> Optional[datetime]:
    """Convert ``dt`` to the family timezone.

    Naive datetimes are first interpreted in ``naive_tz`` (UTC by default; pass ``family_tz()``
    for wall-clock values such as calendar dates).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz or family_tz())
    return dt.astimezone(family_tz())
