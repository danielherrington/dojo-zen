"""Family-timezone date math when the server clock is UTC, as on Cloud Run (DAN-165)."""

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from dojo.config import settings
from dojo.digest import (
    Briefing,
    DigestEngine,
    evaluate_item_temporal_status,
    is_posted_within_24h,
    parse_and_format_timestamp,
    parse_raw_datetime,
)
from dojo.mailer import is_digest_window_allowed
from dojo.tz import family_now, family_tz, to_family_tz

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


@pytest.fixture(autouse=True)
def server_clock_utc(monkeypatch):
    """Run every test with the process timezone forced to UTC, like the Cloud Run container."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    monkeypatch.setattr(settings, "digest_timezone", "America/New_York")
    yield
    monkeypatch.undo()
    time.tzset()


def test_process_clock_really_is_utc():
    assert datetime.now().astimezone().utcoffset().total_seconds() == 0


def test_seven_am_eastern_briefing_is_morning():
    # Mon Nov 16 2026, 7:00 AM EST == 12:00 UTC; the old code labelled this "Evening".
    ref = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    briefing = DigestEngine().synthesize([], [], [], ref_dt=ref)
    assert briefing.period == "Morning"
    assert briefing.generated_at == "Monday, Nov 16, 2026"


def test_briefing_defaults_use_family_clock(monkeypatch):
    fixed = datetime(2026, 11, 16, 12, 30, tzinfo=UTC)  # 7:30 AM ET

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr("dojo.tz.datetime", FrozenDatetime)
    assert family_now().hour == 7
    assert Briefing().period == "Morning"


def test_nine_pm_eastern_post_counts_as_monday():
    # Mon Nov 16 2026, 9:00 PM EST == Tue 02:00 UTC
    post = parse_raw_datetime("2026-11-17T02:00:00Z")
    assert post.tzinfo is not None
    assert post.date().isoformat() == "2026-11-16"
    assert post.strftime("%A") == "Monday"

    # Tuesday 7 AM ET briefing: "tomorrow" in Monday night's post means Today (Tuesday)
    ref = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    res = evaluate_item_temporal_status("Bring sneakers tomorrow for gym", post, ref)
    assert res["is_today"] and not res["is_tomorrow"]
    assert res["target_date"].isoformat() == "2026-11-17"
    assert res["contextualized_summary"].startswith("Today")


def test_display_timestamp_uses_family_clock():
    ref = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)  # Tue 7 AM ET
    text, _ = parse_and_format_timestamp("2026-11-17T02:00:00Z", ref)  # Mon 9 PM ET
    assert text == "Yesterday at 9:00 PM"


def test_sunday_evening_week_ahead_window_in_utc_container():
    # Sun Nov 22 2026, 6:00 PM EST == 23:00 UTC
    sunday_6pm = datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
    allowed, reason = is_digest_window_allowed(sunday_6pm)
    assert allowed and "sunday evening" in reason.lower()

    # Friday-afternoon post is still "fresh" for the Sunday week-ahead briefing
    friday_post = parse_raw_datetime("2026-11-20T20:30:00Z")  # Fri 3:30 PM ET
    assert is_posted_within_24h(friday_post, sunday_6pm)


def test_sunday_morning_eastern_is_still_quiet_even_if_utc_says_afternoon():
    # Sun 11:00 AM EST == 16:00 UTC; the old code compared hour >= 16 in UTC
    allowed, _ = is_digest_window_allowed(datetime(2026, 11, 22, 16, 0, tzinfo=UTC))
    assert not allowed


def test_saturday_night_eastern_is_quiet_even_though_utc_is_sunday():
    # Sat 9:00 PM EST == Sun 02:00 UTC
    allowed, reason = is_digest_window_allowed(datetime(2026, 11, 22, 2, 0, tzinfo=UTC))
    assert not allowed and "saturday" in reason.lower()


def test_date_only_values_are_family_midnight():
    dt = parse_raw_datetime("2026-11-18")
    assert dt.tzinfo is not None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 11, 18, 0)
    assert dt.utcoffset() == ET.utcoffset(datetime(2026, 11, 18))


def test_naive_datetime_is_treated_as_utc():
    assert to_family_tz(datetime(2026, 11, 17, 2, 0)).hour == 21


def test_configurable_timezone(monkeypatch):
    monkeypatch.setattr(settings, "digest_timezone", "America/Los_Angeles")
    assert str(family_tz()) == "America/Los_Angeles"
    # 7 AM Pacific == 15:00 UTC
    briefing = DigestEngine().synthesize([], [], [], ref_dt=datetime(2026, 11, 16, 15, 0, tzinfo=UTC))
    assert briefing.period == "Morning"


def test_unknown_timezone_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "digest_timezone", "Mars/Olympus_Mons")
    assert str(family_tz()) == "America/New_York"
