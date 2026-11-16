"""Tests for the briefing send policy (DAN-98: no Saturday, Sunday evening only)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from dojo.schedule import digest_schedule_decision

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    "local_dt, expected",
    [
        (datetime(2026, 11, 16, 7, 0), True),    # Monday 7am
        (datetime(2026, 11, 20, 7, 0), True),    # Friday 7am
        (datetime(2026, 11, 21, 7, 0), False),   # Saturday 7am
        (datetime(2026, 11, 21, 18, 0), False),  # Saturday evening
        (datetime(2026, 11, 22, 7, 0), False),   # Sunday 7am
        (datetime(2026, 11, 22, 18, 0), True),   # Sunday 6pm week-ahead preview
    ],
)
def test_digest_schedule_decision(local_dt, expected):
    should_send, reason = digest_schedule_decision(local_dt.replace(tzinfo=ET))
    assert should_send is expected
    assert reason


def test_naive_datetime_treated_as_family_local():
    assert digest_schedule_decision(datetime(2026, 11, 21, 7, 0))[0] is False


def test_utc_input_is_converted_to_family_timezone():
    # Sunday 23:00 UTC == Sunday 6pm ET -> send
    assert digest_schedule_decision(datetime(2026, 11, 22, 23, 0, tzinfo=timezone.utc))[0] is True
    # Saturday 02:00 UTC == Friday 9pm ET -> weekday, send
    assert digest_schedule_decision(datetime(2026, 11, 21, 2, 0, tzinfo=timezone.utc))[0] is True
    # Sunday 12:00 UTC == Sunday 7am ET -> skip
    assert digest_schedule_decision(datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc))[0] is False


def test_briefing_period_uses_family_timezone(monkeypatch):
    import dojo.digest as digest_mod

    # 7am ET is 12:00 UTC; the label must still be "Morning" on a UTC server.
    monkeypatch.setattr(digest_mod, "local_now", lambda: datetime(2026, 11, 16, 7, 0, tzinfo=ET))
    assert digest_mod.Briefing().period == "Morning"
    monkeypatch.setattr(digest_mod, "local_now", lambda: datetime(2026, 11, 22, 18, 0, tzinfo=ET))
    assert digest_mod.Briefing().period == "Evening"
