"""Export raw-store bars to LEAN forex QuoteBar files (backlog B-14).

LEAN format, checked against quantconnect/lean:18206 sample data and a probe run (2026-10-26):
- minute: forex/oanda/minute/<sym>/YYYYMMDD_quote.zip -> YYYYMMDD_<sym>_minute_quote.csv,
  first column = milliseconds since midnight of that date.
- hour:   forex/oanda/hour/<sym>.zip -> <sym>.csv, first column = "YYYYMMDD HH:MM".
- Columns: time, bid O,H,L,C, bid size, ask O,H,L,C, ask size. Forex prices are not scaled.
- The time column is the bar START (open) time in the market's data timezone, which is UTC for
  oanda (market-hours-database dataTimeZone). LEAN sets EndTime = Time + period and hands the
  bar to the algorithm at EndTime. So our open-time UTC bars are written unshifted; shifting
  them by one period would make every bar arrive one period late.

ponytail: data goes under the `oanda` market so LEAN's market-hours and symbol-properties
entries for oanda apply. Those are OANDA's session calendar (Sun 17:03 - Fri 16:58 New York)
and OANDA's contract facts, not our broker's. Bars outside that calendar are dropped or
fill-forwarded by LEAN. Upgrade path: our own market id with entries generated from SymbolSpec
and the broker's session hours.
"""

import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import rawstore
from .contracts import Resolution
from .runs import sha256_file

MARKET = "oanda"
EXPORT_JSON = "export.json"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # fixed, so the same input gives byte-identical zips
SPREAD_RULE = ("Per-bar spread_points * point applied to all four prices (open, high, low, close). "
               "Approximation: the real intrabar spread varies; ask high/low are not observed.")


def _manifests(refs: list[dict]) -> list[dict]:
    return [json.loads((Path(r["path"]) / rawstore.MANIFEST).read_text()) for r in refs]


def _write_zip(path: Path, inner: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo(inner, _ZIP_TIME), "\n".join(lines) + "\n", zipfile.ZIP_DEFLATED)
    path.write_bytes(buf.getvalue())


def export(root: Path, out: Path, source: str, symbol: str, resolution: Resolution, start: date, end: date,
           fixed_spread_points: int | None = None) -> dict:
    """Write LEAN QuoteBar files for days [start, end] under out/forex/oanda, return the export entry."""
    res = Resolution(resolution)
    if res not in (Resolution.MINUTE, Resolution.HOUR):
        raise ValueError(f"resolution {res} not supported (minute, hour)")
    table, refs = rawstore.read_range(root, "bars", source, symbol, res, start, end)
    if not refs:
        raise FileNotFoundError(f"no bars for {source}/{symbol}/{res} in {start}..{end}")
    metas = _manifests(refs)
    sides = {m["provenance"].get("price_side") for m in metas}
    points = {m["provenance"].get("symbol_spec", {}).get("point") for m in metas}
    digits = {m["provenance"].get("symbol_spec", {}).get("digits") for m in metas}
    if len(sides) != 1 or sides - {"bid", "mid"}:
        raise ValueError(f"price_side must be one of bid/mid and the same for all batches, got {sides}")
    if len(points) != 1 or None in points or len(digits) != 1 or None in digits:
        raise ValueError(f"symbol_spec point/digits missing or inconsistent across batches: {points} {digits}")
    side, point, dig = sides.pop(), points.pop(), digits.pop()

    rows = table.to_pylist()
    missing = sum(r["spread_points"] is None for r in rows)
    if missing and fixed_spread_points is None:
        raise ValueError(f"{missing} bars have null spread_points; pass fixed_spread_points to export anyway")
    if fixed_spread_points is not None and fixed_spread_points < 0:
        raise ValueError("fixed_spread_points must be >= 0")
    nd = dig if side == "bid" else dig + 1  # mid +/- half a point needs one more decimal

    def line(r) -> tuple[date, str]:
        sp = (r["spread_points"] if r["spread_points"] is not None else fixed_spread_points) * point
        lo, hi = (0.0, sp) if side == "bid" else (-sp / 2, sp / 2)
        bid = [r[k] + lo for k in ("open", "high", "low", "close")]
        ask = [r[k] + hi for k in ("open", "high", "low", "close")]
        t = r["time"].astimezone(timezone.utc)  # oanda dataTimeZone is UTC; bar open time, no shift
        stamp = (str((t - datetime(t.year, t.month, t.day, tzinfo=timezone.utc)) // timedelta(milliseconds=1))
                 if res == Resolution.MINUTE else t.strftime("%Y%m%d %H:%M"))
        px = lambda v: f"{v:.{nd}f}"
        return t.date(), ",".join([stamp, *map(px, bid), "0", *map(px, ask), "0"])

    sym = symbol.lower()
    base = Path(out) / "forex" / MARKET / res.value
    files = []
    if res == Resolution.MINUTE:
        for old in (base / sym).glob("*_quote.zip"):  # our own previous output for this symbol
            old.unlink()
        by_day: dict[date, list[str]] = {}
        for r in rows:
            d, ln = line(r)
            by_day.setdefault(d, []).append(ln)
        for d, lines in sorted(by_day.items()):
            p = base / sym / f"{d:%Y%m%d}_quote.zip"
            _write_zip(p, f"{d:%Y%m%d}_{sym}_minute_quote.csv", lines)
            files.append(p)
    else:
        p = base / f"{sym}.zip"
        _write_zip(p, f"{sym}.csv", [line(r)[1] for r in rows])
        files.append(p)

    entry = {
        "source": source, "symbol": symbol, "resolution": res.value,
        "start": start.isoformat(), "end": end.isoformat(), "rows": len(rows),
        "price_side": side, "point": point, "digits": dig,
        "spread": {"rule": SPREAD_RULE,
                   "derivation": "ask = bid + spread" if side == "bid" else "bid = mid - spread/2, ask = mid + spread/2",
                   "source": "per-bar spread_points" if not missing else "per-bar spread_points, fixed where null",
                   "fixed_spread_points": fixed_spread_points if missing else None,
                   "bars_with_fixed_spread": missing},
        "lean": {"market": MARKET, "data_timezone": "UTC",
                 "time_convention": "bar open time, unshifted; LEAN derives EndTime = Time + period",
                 "files": [{"path": f.relative_to(out).as_posix(), "sha256": sha256_file(f)} for f in files]},
        "manifest_refs": refs,
    }
    ej = Path(out) / EXPORT_JSON
    exports = json.loads(ej.read_text())["exports"] if ej.exists() else []
    exports = [e for e in exports if (e["symbol"], e["resolution"]) != (symbol, res.value)] + [entry]
    ej.write_text(json.dumps({"exports": exports}, indent=2))
    return entry
