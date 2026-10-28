import json
from datetime import datetime, timedelta, timezone

import pytest

from fxra import econ_calendar as ec
from fxra.trade_journal import Journal

FOMC = datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
OFFSET = 2 * 3600  # broker server clock = UTC+2


def raw_file(events, generated=FOMC):
    return {"generated_server": int(generated.timestamp()) + OFFSET + 7, "generated_gmt": int(generated.timestamp()),
            "events": events}


def fomc(impact=1, actual=4.0):
    return {"id": 1, "event_id": 840030016, "time_server": int(FOMC.timestamp()) + OFFSET, "time_mode": 0,
            "currency": "USD", "name": "Fed Interest Rate Decision", "code": "fed-rate", "importance": 3,
            "actual": actual, "forecast": 4.0, "previous": 4.0, "impact": impact}


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.sqlite")


def bars(n=20, start=FOMC - timedelta(minutes=15 * 18), last=None, prev=None):
    out = [{"time": (start + timedelta(minutes=15 * i)).isoformat(), "open": 1.1, "high": 1.1005, "low": 1.0995,
            "close": 1.1} for i in range(n)]
    if prev:
        out[-2].update(prev)
    if last:
        out[-1].update(last)
    return out


def test_parse_converts_server_time_to_utc():
    (e,) = ec.parse(raw_file([fomc()]))
    assert e["time"] == FOMC.isoformat() and e["exact"] and e["importance"] == 3


def test_refresh_stores_events_and_reports_freshness(journal, tmp_path):
    path = tmp_path / "fxra_calendar.json"
    assert ec.refresh(journal, path, FOMC)["status"] == "missing"
    path.write_text(json.dumps(raw_file([fomc()])), encoding="utf-8")
    assert ec.refresh(journal, path, FOMC + timedelta(minutes=5))["status"] == "ok"
    assert ec.refresh(journal, path, FOMC + timedelta(hours=2))["status"] == "stale"
    assert [e["name"] for e in journal.events()] == ["Fed Interest Rate Decision"]


def test_context_phases_and_symbols():
    events = ec.parse(raw_file([fomc()]))
    assert ec.context(events, "EURUSD", FOMC - timedelta(minutes=20))["event"] == "pre high"
    c = ec.context(events, "XAUUSD", FOMC + timedelta(minutes=10))
    assert c["event"] == "release high" and c["event_minutes"] == 10 and c["event_impact"] == "positive"
    assert ec.context(events, "EURUSD", FOMC + timedelta(minutes=60))["event"] == "post high"
    assert ec.context(events, "EURUSD", FOMC + timedelta(hours=5)) == {"event": "none"}


def test_currency_side():
    assert ec.currency_side("EURUSD", "USD", True) == "sell"
    assert ec.currency_side("USDJPY", "USD", True) == "buy"
    assert ec.currency_side("XAUUSD", "USD", False) == "buy"
    assert ec.currency_side("EURUSD", "JPY", True) is None


def test_news_patterns_release_bar():
    events = ec.parse(raw_file([fomc(impact=1)]))
    b = bars(last={"open": 1.1, "high": 1.1001, "low": 1.0960, "close": 1.0965})  # big down bar at 18:00
    assert ec.news_patterns(events, "EURUSD", FOMC, b) == {"news_momentum": "sell", "news_surprise": "sell"}
    assert ec.news_patterns(events, "USDJPY", FOMC, b)["news_surprise"] == "buy"
    assert ec.news_patterns([], "EURUSD", FOMC, b) == {}


def test_news_followthrough_waits_for_confirmation():
    events = ec.parse(raw_file([fomc(impact=0)]))
    spike = {"open": 1.1, "high": 1.1001, "low": 1.0960, "close": 1.0965}
    b = bars(start=FOMC - timedelta(minutes=15 * 17), prev=spike,
             last={"open": 1.0965, "high": 1.0966, "low": 1.0950, "close": 1.0952})
    assert ec.news_patterns(events, "EURUSD", FOMC + timedelta(minutes=15), b) == {"news_followthrough": "sell"}
    b[-1].update({"close": 1.0970, "high": 1.0975})  # reverses: no confirmation
    assert ec.news_patterns(events, "EURUSD", FOMC + timedelta(minutes=15), b) == {}
