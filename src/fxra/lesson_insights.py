"""Periodic digest of H-002 demo results for Hindsight: the layer later turns actually recall.

Single-trade lessons are n=1 and Hindsight cannot count them. This module counts
from the journal (the source of truth) and retains one document per pattern plus
an overview, each with a fixed id (`fxra-insight-...`) that is replaced when the
numbers change. Every line carries n and a sample-size label; results are
exploratory in-sample observations, never evidence of edge.
"""

import hashlib
from datetime import datetime

from .trade_journal import PIP_SIZES, TRADES_SQL, Journal, confluence

DIMENSIONS = (("session", "By session"), ("symbol", "By symbol"), ("hour", "By UTC hour"), ("vol", "By volatility"),
              ("h1_trend", "By H1 trend"), ("dow", "By weekday"), ("arm", "By arm"),
              ("confluence", "By signal agreement"))
DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def label(n: int) -> str:
    return "noise" if n < 10 else "weak" if n < 30 else "in-sample"


def closed_trades(journal: Journal, assumed_cost_pips: float) -> list[dict]:
    """Closed H-002 trades with entry conditions and result in R after the assumed cost."""
    out = []
    for t in journal._rows(TRADES_SQL + " WHERE t.net IS NOT NULL AND g.pattern IS NOT NULL ORDER BY t.close_time"):
        info = journal.entry_info(t["entry_id"])
        f, pip = info["features"], PIP_SIZES.get(t["symbol"], 0.0001)
        moved = (t["close_price"] - t["open_price"]) * (1 if t["side"] == "buy" else -1) - assumed_cost_pips * pip
        risk_dist = abs(t["open_price"] - info["stop_loss"]) if info["stop_loss"] else None
        open_dow = datetime.fromisoformat(t["open_time"]).strftime("%a")
        out.append({**t, "r": moved / risk_dist if risk_dist else None, "vol": f.get("vol"),
                    "h1_trend": f.get("h1_trend"), "dow": f.get("dow") or open_dow,
                    "confluence": confluence(f, t["side"]),
                    "hour": f"{t['hour_utc']:02d}" if t["hour_utc"] is not None else None})
    return out


def stats(rows: list[dict]) -> dict:
    rs = [t["r"] for t in rows if t["r"] is not None]
    wins = sum(t["net"] > 0 for t in rows)
    return {"n": len(rows), "win_rate": wins / len(rows), "avg_r": sum(rs) / len(rs) if rs else None,
            "net": sum(t["net"] for t in rows)}


def line(name: str, s: dict) -> str:
    avg = f"{s['avg_r']:+.2f}R" if s["avg_r"] is not None else "n/a R"
    return f"{name}: n={s['n']}, win {s['win_rate']:.0%}, avg {avg}, net {s['net']:+.2f} USD [{label(s['n'])}]"


def breakdown(rows: list[dict], key: str) -> list[str]:
    groups: dict = {}
    for t in rows:
        if t.get(key) is not None:
            groups.setdefault(t[key], []).append(t)
    order = (lambda k: DOW.index(k) if k in DOW else 9) if key == "dow" else str
    return [line(str(k), stats(v)) for k, v in sorted(groups.items(), key=lambda kv: order(kv[0]))]


def digest(journal: Journal, assumed_cost_pips: float = 0.8) -> list[dict]:
    """Documents to retain: an overview and one per traded pattern."""
    rows = closed_trades(journal, assumed_cost_pips)
    if not rows:
        return []
    header = (f"Digest of {len(rows)} closed H-002 demo trades through {rows[-1]['close_time'][:16]}Z "
              f"(R after an assumed {assumed_cost_pips} pip cost; n<10 noise, n<30 weak). "
              f"Exploratory in-sample observations, not evidence of edge; numbers come from the demo journal.")
    sections = lambda rs, dims: "\n".join(f"{title}: " + "; ".join(breakdown(rs, key))
                                          for key, title in dims if breakdown(rs, key))
    by_pattern: dict = {}
    for t in rows:
        by_pattern.setdefault(t["pattern"], []).append(t)
    ranked = sorted(by_pattern.items(), key=lambda kv: -(stats(kv[1])["avg_r"] or 0))
    combos: dict = {}
    for t in rows:
        combos.setdefault(f"{t['pattern']} / {t['session']}", []).append(t)
    solid = [line(k, stats(v)) for k, v in sorted(combos.items(), key=lambda kv: -(stats(kv[1])["avg_r"] or 0))
             if len(v) >= 10]
    overview = (f"H-002 overview. {header}\nOverall: {line('all', stats(rows))}\n"
                f"By pattern: " + "; ".join(line(p, stats(v)) for p, v in ranked) + "\n"
                + sections(rows, DIMENSIONS) + "\n"
                + "Pattern x session with n>=10: " + ("; ".join(solid) if solid else "none yet"))
    docs = [{"document": "fxra-insight-overview", "content": overview, "tags": ["demo", "insight", "H-002", "overview"]}]
    for pattern, rs in ranked:
        content = (f"H-002 pattern {pattern}. {header}\nOverall: {line(pattern, stats(rs))}\n"
                   + sections(rs, DIMENSIONS))
        docs.append({"document": f"fxra-insight-pattern-{pattern.replace('_', '-')}", "content": content,
                     "tags": ["demo", "insight", "H-002", pattern]})
    from .playbook import document  # the playbook counts from the same rows
    if (pb := document(journal, rows)) is not None:
        docs.append(pb)
    return docs


def sync(journal: Journal, backend, now: datetime, lock=None, assumed_cost_pips: float = 0.8) -> dict:
    """Retain digest documents whose content changed; network calls run outside `lock`."""
    import contextlib
    guard = lock or contextlib.nullcontext()
    with guard:
        docs, known = digest(journal, assumed_cost_pips), journal.insight_hashes()
    sent, failed = [], 0
    for d in docs:
        h = hashlib.sha256(d["content"].encode()).hexdigest()
        if known.get(d["document"]) == h:
            continue
        try:
            backend.retain_lesson(d["document"], d["content"], now.isoformat(), d["tags"])
        except Exception:
            failed += 1
            break  # Hindsight unreachable: retry at the next digest
        with guard:
            journal.mark_insight(d["document"], h, now)
        sent.append(d["document"])
    return {"retained": sent, "failed": failed, "documents": len(docs), "at": now.isoformat()}
