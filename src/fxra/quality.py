"""Data quality report for the raw bars store (B-13, risks R-02, R-03, R-08).

Report only: reads through `rawstore.read_range`, changes nothing, judges nothing.
The expected session calendar is weekly (open weekday+time to close weekday+time in an IANA tz,
default Sun 17:00 to Fri 17:00 America/New_York). Holidays are not modelled; they show as gaps.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import rawstore
from .contracts import Resolution

STEP = {Resolution.MINUTE: 60, Resolution.HOUR: 3600}
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UTC = timezone.utc
FOOTER = ("Holidays are not modelled: they appear as gaps. Numbers only, no judgement. "
          "Missing = expected bar open times (inside the session) with no row.")


def parse_week_point(s: str) -> tuple[int, time]:
    """'Sun 17:00' -> (6, 17:00)."""
    d, t = s.split()
    return _DAYS.index(d[:3].lower()), time.fromisoformat(t)


def sessions(start: date, end: date, week_open: str, week_close: str, tz: str) -> list[tuple[datetime, datetime]]:
    """Session [open, close) intervals in UTC that may overlap UTC days start..end."""
    z = ZoneInfo(tz)
    (owd, ot), (cwd, ct) = parse_week_point(week_open), parse_week_point(week_close)
    out = []
    d = start - timedelta(days=9)
    while d <= end + timedelta(days=1):
        if d.weekday() == owd:
            cd = d + timedelta(days=(cwd - owd) % 7)
            if cd == d and ct <= ot:
                cd += timedelta(days=7)
            out.append((datetime.combine(d, ot, z).astimezone(UTC), datetime.combine(cd, ct, z).astimezone(UTC)))
        d += timedelta(days=1)
    return out


def _expected(day: date, sess, step: int) -> list[int]:
    d0 = int(datetime.combine(day, time(), UTC).timestamp())
    d1 = d0 + 86400
    out = []
    for o, c in sess:
        lo, hi = max(int(o.timestamp()), d0), min(int(c.timestamp()), d1)
        lo += -lo % step  # first grid point >= lo
        out.extend(range(lo, hi, step))
    return out


def _pct(v: list, p: int):
    """Nearest-rank percentile of a sorted list."""
    return v[min(len(v) - 1, max(0, -(-len(v) * p // 100) - 1))] if v else None


def _spread(vals: list) -> dict:
    v = sorted(x for x in vals if x is not None)
    return {"null": len(vals) - len(v), "p50": _pct(v, 50), "p95": _pct(v, 95), "max": v[-1] if v else None}


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def report(root: Path, source: str, symbol: str, resolution: Resolution, start: date, end: date,
           week_open: str = "Sun 17:00", week_close: str = "Fri 17:00", session_tz: str = "America/New_York") -> dict:
    step = STEP[resolution]
    table, refs = rawstore.read_range(Path(root), "bars", source, symbol, resolution, start, end)
    rows = table.to_pylist()
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["time"].astimezone(UTC).date()].append(r)
    sess = sessions(start, end, week_open, week_close, session_tz)

    days, tot_exp, tot_miss, no_data = [], 0, 0, []
    for i in range((end - start).days + 1):
        day = start + timedelta(days=i)
        rs = by_day.get(day, [])
        have = {int(r["time"].timestamp()) for r in rs}
        exp = _expected(day, sess, step)
        missing = [t for t in exp if t not in have]
        longest_start, longest = None, 0
        run_start, run = None, 0
        for t in exp:
            if t in have:
                run = 0
                continue
            if run == 0:
                run_start = t
            run += 1
            if run > longest:
                longest_start, longest = run_start, run
        if exp and not rs:
            no_data.append(day.isoformat())
        tot_exp += len(exp)
        tot_miss += len(missing)
        days.append({
            "date": day.isoformat(), "rows": len(rs), "expected": len(exp), "missing": len(missing),
            "longest_gap_start": _iso(longest_start) if longest_start is not None else None,
            "longest_gap_bars": longest, "longest_gap_seconds": longest * step,
            "outside_session": len(have - set(exp)), "flat_bars": sum(r["high"] == r["low"] for r in rs),
            "spread_points": _spread([r["spread_points"] for r in rs]),
        })

    by_hour = defaultdict(list)
    for r in rows:
        by_hour[r["time"].astimezone(UTC).hour].append(r["spread_points"])
    spread_by_hour = [{"hour_utc": h, "n": len(by_hour[h]), **_spread(by_hour[h])} for h in range(24)]

    # Weekend boundaries: any gap over 12h between consecutive bars, compared with the calendar's edges.
    times = sorted(int(r["time"].timestamp()) for r in rows)
    ses = sorted((int(o.timestamp()), int(c.timestamp())) for o, c in sess)
    boundaries = []
    for a, b in zip(times, times[1:]):
        if b - a <= 12 * 3600:
            continue
        entry = {"last_before": _iso(a), "first_after": _iso(b), "gap_hours": round((b - a) / 3600, 2)}
        close = next((c for _, c in ses if c >= a + step), None)
        nxt_open = next((o for o, _ in ses if o > a), None)
        if close is not None:
            entry["expected_last_bar"] = _iso(close - step)
            entry["last_before_offset_min"] = (a - (close - step)) // 60
        if nxt_open is not None:
            entry["expected_first_bar"] = _iso(nxt_open)
            entry["first_after_offset_min"] = (b - nxt_open) // 60
        boundaries.append(entry)

    return {
        "source": source, "symbol": symbol, "resolution": str(resolution), "start": start.isoformat(),
        "end": end.isoformat(), "partitions_read": len(refs),
        "calendar": {"week_open": week_open, "week_close": week_close, "session_tz": session_tz,
                     "holidays_modelled": False},
        "summary": {"rows": len(rows), "expected": tot_exp, "missing": tot_miss,
                    "missing_pct": round(100 * tot_miss / tot_exp, 3) if tot_exp else None,
                    "days_with_no_data": no_data},
        "days": days, "spread_by_utc_hour": spread_by_hour, "weekend_boundaries": boundaries, "footer": FOOTER,
    }


def to_markdown(r: dict) -> str:
    c, s = r["calendar"], r["summary"]

    def f(v):
        return "-" if v is None else v

    L = [f"# Data quality: {r['source']} {r['symbol']} {r['resolution']} {r['start']}..{r['end']}", "",
         f"Calendar: open {c['week_open']}, close {c['week_close']}, tz {c['session_tz']}; holidays not modelled.",
         f"Partitions read: {r['partitions_read']}. Rows {s['rows']}, expected {s['expected']}, "
         f"missing {s['missing']} ({f(s['missing_pct'])}%).",
         f"Days with no data: {', '.join(s['days_with_no_data']) or 'none'}", "",
         "## Per day", "",
         "| date | rows | expected | missing | longest gap start | gap bars | outside | flat | spr null | spr p50 | spr p95 | spr max |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in r["days"]:
        sp = d["spread_points"]
        L.append(f"| {d['date']} | {d['rows']} | {d['expected']} | {d['missing']} | {f(d['longest_gap_start'])} | "
                 f"{d['longest_gap_bars']} | {d['outside_session']} | {d['flat_bars']} | {sp['null']} | "
                 f"{f(sp['p50'])} | {f(sp['p95'])} | {f(sp['max'])} |")
    L += ["", "## Spread by UTC hour", "", "| hour | n | null | p50 | p95 | max |", "|---|---|---|---|---|---|"]
    L += [f"| {h['hour_utc']:02d} | {h['n']} | {h['null']} | {f(h['p50'])} | {f(h['p95'])} | {f(h['max'])} |"
          for h in r["spread_by_utc_hour"]]
    L += ["", "## Weekend boundaries (gaps over 12h)", "",
          "| last before | expected last bar | offset min | first after | expected first bar | offset min |",
          "|---|---|---|---|---|---|"]
    L += [f"| {b['last_before']} | {f(b.get('expected_last_bar'))} | {f(b.get('last_before_offset_min'))} | "
          f"{b['first_after']} | {f(b.get('expected_first_bar'))} | {f(b.get('first_after_offset_min'))} |"
          for b in r["weekend_boundaries"]]
    return "\n".join(L + ["", r["footer"], ""])
