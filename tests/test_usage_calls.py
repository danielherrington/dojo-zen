"""DAN-284: per-feature calls, errors, zero results and durations (no names, message text or emails)."""

import asyncio
import io
import json
from collections import Counter
from datetime import datetime, timezone

import pytest

from dojo import analytics, usage_v1


class CounterDB:
    def __init__(self):
        self.counts = Counter()

    def increment_usage(self, day, name, amount=1):
        self.counts[name] += amount


@pytest.fixture
def events(monkeypatch):
    buf = io.StringIO()
    real = usage_v1.emit
    monkeypatch.setattr(usage_v1, "emit", lambda *a, **k: real(*a, **{**k, "stream": buf}))
    return lambda: [json.loads(line) for line in buf.getvalue().splitlines()]


def test_query_tokens_drop_names_emails_numbers_and_stopwords():
    toks = usage_v1.query_tokens("When is the Zed bake sale? email jo@example.test or 305-555-1212 Kid",
                                 names=["Kid Test"])
    assert toks == ["bake", "sale", "email"]
    assert usage_v1.query_tokens("Field trip permission slip for Ana", names=[]) == [
        "field", "trip", "permission", "slip"]


def test_duration_buckets():
    assert [usage_v1.duration_bucket(ms) for ms in (10, 50, 51, 2400, 99999)] == ["50", "50", "100", "2500", "inf"]


def test_record_call_counts_zero_result_without_query_text_in_events(events):
    db = CounterDB()
    usage_v1.record_call(db, "search_feed", "mcp", 120, result=[], query="sneakers for gym class",
                         background=False)
    assert db.counts == Counter({"tool__search_feed": 1, "dur__search_feed__250": 1, "zero__search_feed": 1,
                                 "zq__sneakers": 1, "zq__gym": 1, "zq__class": 1})
    out = events()
    assert [e["event"] for e in out] == ["feature_used", "zero_result"]
    assert out[0]["props"] == {"duration_ms": 120, "result_count": 0}
    assert "sneakers" not in json.dumps(out)


def test_record_call_error_uses_code_only(events):
    db = CounterDB()
    usage_v1.record_call(db, "school_today", "mcp", 30, error_code="KeyError", background=False)
    assert db.counts["error__school_today__keyerror"] == 1 and "zero__school_today" not in db.counts
    (ev,) = events()
    assert ev["event"] == "error" and ev["props"] == {"duration_ms": 30, "code": "keyerror"}


def test_record_error_never_carries_the_message(events):
    db = CounterDB()
    usage_v1.record_error(db, "daily_digest", "cron", RuntimeError("SMTP refused for family@example.test"),
                          background=False)
    assert dict(db.counts) == {"error__daily_digest__runtimeerror": 1}
    assert "example.test" not in json.dumps(events())


def test_analytics_off_records_nothing(monkeypatch, events):
    from dojo.config import settings
    monkeypatch.setattr(settings, "analytics_enabled", False)
    db = CounterDB()
    usage_v1.record_call(db, "ask", "web", 5, result=None, background=False)
    usage_v1.record_error(db, "ask", "web", "X", background=False)
    assert not db.counts and events() == []


def test_result_count_shapes():
    assert usage_v1.result_count({"assignments": []}) == 0
    assert usage_v1.result_count({"items": [1, 2]}) == 2
    assert usage_v1.result_count("briefing") == 1
    assert usage_v1.result_count({"answer": "x"}) is None


def test_mcp_tools_keep_their_public_surface_and_are_tracked(monkeypatch):
    from dojo import mcp_server

    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert set(tools) == {
        "get_daily_briefing", "get_action_items", "get_upcoming_dates", "ask_classroom_assistant", "search_feed",
        "get_recent_feed", "get_feed_item", "get_enrolled_children", "school_today", "school_upcoming", "pickup_owner"}
    assert tools["search_feed"].input_schema["required"] == ["query"]
    assert set(tools["pickup_owner"].input_schema["properties"]) == {"date", "kind"}
    assert all(getattr(getattr(mcp_server, n), "__wrapped__", None) for n in tools)

    calls = []
    monkeypatch.setattr(usage_v1, "record_call", lambda *a, **k: calls.append((a, k)))
    mcp_server.search_feed(query="zzqx nothing matches", limit=3)
    (args, kwargs) = calls[-1]
    assert args[1:3] == ("search_feed", "mcp") and kwargs["query"] == "zzqx nothing matches"


def test_tool_errors_are_recorded_and_still_raised(monkeypatch):
    from dojo import mcp_server

    calls = []
    monkeypatch.setattr(usage_v1, "record_call", lambda *a, **k: calls.append(k))

    @mcp_server._tracked
    def boom(question: str) -> dict:
        raise KeyError("x")

    with pytest.raises(KeyError):
        boom(question="anything")
    assert calls[-1]["error_code"] == "KeyError"


def test_report_shows_calls_errors_zero_results_terms_and_percentiles():
    usage = [{"day": "2026-11-20", "mcp": 10, "tool__search_feed": 4, "tool__pickup_owner": 2,
              "zero__search_feed": 2, "zq__sneakers": 3, "zq__rare": 2,
              "error__daily_digest__runtimeerror": 1,
              "dur__search_feed__100": 3, "dur__search_feed__2500": 1}]
    r = analytics.summarize([], [], usage, days=7, now=datetime(2026, 11, 21, tzinfo=timezone.utc))
    f = r["features"]
    assert f["calls"] == {"search_feed": 4, "pickup_owner": 2}
    assert f["errors"] == {"daily_digest: runtimeerror": 1}
    assert f["zero_results"]["search_feed"] == {"count": 2, "rate": 0.5}
    assert f["zero_result_terms"] == {"sneakers": 3}  # below 3 hidden
    assert f["duration_ms"]["search_feed"] == {"calls": 4, "p50": "<=100", "p95": "<=2500"}
    assert r["usage"]["totals"] == {"mcp": 10}  # prefixed counters stay out of channel totals
    text = analytics.format_report({**r, "gaps": analytics.REPORT_GAPS})
    assert "search_feed" in text and "sneakers" in text and "rare" not in text
