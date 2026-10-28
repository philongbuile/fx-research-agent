"""Economic calendar from the MT5 terminal (D-40): news context for every trade and news patterns.

The first-party MQL5 service `mt5_services/FxraCalendarExport.mq5` writes moderate and high
importance events for USD, EUR, GBP, JPY and AUD to the terminal's Common\\Files folder. This module
reads that file (times are trade-server time; the file carries server and GMT clocks to convert),
keeps every event in the journal so past trades stay taggable, and derives:

- `context`: the nearest relevant event around an entry ("pre"/"release"/"post" + importance), so
  stats and the playbook can compare trades near news with trades away from it;
- `news_patterns`: three exploration patterns that trade the news instead of avoiding it.

MT5's `impact` says whether the released value was good (1) or bad (2) for the event's currency.
Nothing here predicts news; it records when news happened and what price did around it.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PATH = (Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal" / "Common" / "Files"
                / "fxra_calendar.json")
SYMBOL_CURRENCIES = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"),
                     "AUDUSD": ("AUD", "USD"), "XAUUSD": ("XAU", "USD")}
HIGH, MODERATE = 3, 2
IMPACT = {1: "positive", 2: "negative"}
NEWS_PATTERNS = ("news_momentum", "news_surprise", "news_followthrough")
STALE_AFTER = timedelta(minutes=30)


def parse(raw: dict) -> list[dict]:
    """Calendar file -> events with UTC times. Server offset is rounded to 15 minutes."""
    offset = round((raw["generated_server"] - raw["generated_gmt"]) / 900) * 900
    out = []
    for e in raw.get("events", []):
        out.append({"id": int(e["id"]), "time": datetime.fromtimestamp(e["time_server"] - offset, timezone.utc).isoformat(),
                    "exact": int(e.get("time_mode", 0)) == 0, "currency": e["currency"], "name": e["name"],
                    "code": e.get("code"), "importance": int(e["importance"]), "actual": e.get("actual"),
                    "forecast": e.get("forecast"), "previous": e.get("previous"), "impact": int(e.get("impact") or 0)})
    return out


_SEEN: dict = {}  # path -> file mtime and generation time of the last load


def refresh(journal, path: Path = DEFAULT_PATH, now: datetime | None = None) -> dict:
    """Load the export when it changed, store its events, and report whether the feed is fresh."""
    now, path, _seen = now or datetime.now(timezone.utc), Path(path), _SEEN
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {"status": "missing", "path": str(path)}
    if _seen.get(str(path)) != mtime:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        journal.store_events(parse(raw))
        _seen[str(path)] = mtime
        _seen[str(path) + ":generated"] = datetime.fromtimestamp(raw["generated_gmt"], timezone.utc)
    generated = _seen[str(path) + ":generated"]
    return {"status": "stale" if now - generated > STALE_AFTER else "ok", "generated": generated.isoformat()}


def _when(e: dict) -> datetime:
    return datetime.fromisoformat(e["time"])


def _relevant(events: list[dict], symbol: str, min_importance: int = MODERATE) -> list[dict]:
    ccys = SYMBOL_CURRENCIES.get(symbol, ())
    return [e for e in events or () if e["currency"] in ccys and e["exact"] and e["importance"] >= min_importance]


def context(events: list[dict], symbol: str, at: datetime, window_min: int = 120) -> dict:
    """The most important event within +-window of `at` for the symbol's currencies (nearest on ties)."""
    near = [(e, (at - _when(e)).total_seconds() / 60) for e in _relevant(events, symbol)]
    near = [(e, m) for e, m in near if -window_min <= m <= window_min]
    if not near:
        return {"event": "none"}
    e, minutes = max(near, key=lambda em: (em[0]["importance"], -abs(em[1])))
    phase = "pre" if minutes < 0 else "release" if minutes < 30 else "post"
    return {"event": f"{phase} {'high' if e['importance'] >= HIGH else 'moderate'}", "event_name": e["name"],
            "event_currency": e["currency"], "event_minutes": round(minutes), "event_impact": IMPACT.get(e["impact"])}


def currency_side(symbol: str, currency: str, stronger: bool) -> str | None:
    """Side on `symbol` that profits when `currency` strengthens (or weakens)."""
    base, quote = SYMBOL_CURRENCIES.get(symbol, (symbol[:3], symbol[3:]))
    if currency == base:
        return "buy" if stronger else "sell"
    if currency == quote:
        return "sell" if stronger else "buy"
    return None


def _range_atr(bars: list[dict], n: int = 14) -> float:
    tr = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
          for p, b in zip(bars, bars[1:])]
    return sum(tr[-n:]) / min(n, len(tr)) if tr else 0.0


def news_patterns(events: list[dict], symbol: str, bar_time: datetime, bars: list[dict]) -> dict[str, str]:
    """Patterns for the M15 bar opening at `bar_time` (the last closed bar), around high-importance news.

    - news_momentum: a release inside this bar and the bar's range >= ATR before it: follow the bar.
    - news_surprise: a release inside this bar whose value MT5 marks good/bad for its currency: trade
      that currency's direction.
    - news_followthrough: a release in the previous bar, and this bar continues that bar's direction:
      enter once the direction is confirmed (wait out the first spike).
    """
    high = _relevant(events, symbol, HIGH)
    if not high or len(bars) < 17:
        return {}
    end = bar_time + timedelta(minutes=15)
    now_bar = [e for e in high if bar_time <= _when(e) < end]
    prev_bar = [e for e in high if bar_time - timedelta(minutes=15) <= _when(e) < bar_time]
    last, prev = bars[-1], bars[-2]
    a = _range_atr(bars[-17:-1])
    out = {}
    if now_bar:
        if a and last["high"] - last["low"] >= a and last["close"] != last["open"]:
            out["news_momentum"] = "buy" if last["close"] > last["open"] else "sell"
        for e in now_bar:
            if e["impact"] in IMPACT and (side := currency_side(symbol, e["currency"], e["impact"] == 1)):
                out["news_surprise"] = side
                break
    elif prev_bar:
        d_prev, d_last = prev["close"] - prev["open"], last["close"] - last["open"]
        if a and prev["high"] - prev["low"] >= a and d_prev * d_last > 0:
            out["news_followthrough"] = "buy" if d_last > 0 else "sell"
    return out
