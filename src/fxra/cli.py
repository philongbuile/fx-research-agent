"""fxra command line. Run `uv run fxra --help`."""

import argparse
import json
import sys
import tomllib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

from . import lean, quality, rawstore, risk, runs
from .adapters import CsvBarsAdapter, DryRunExecution
from .contracts import AccountSnapshot, OrderIntent, Quote, Resolution, SymbolSpec


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def cmd_backtest(a) -> int:
    run_dir = runs.new_run(a.config, a.runs_root, lean.image_ref())
    record = json.loads((run_dir / "run.json").read_text())
    project = runs.REPO / Path(record["code"]["strategy_path"]).parent
    code = lean.backtest(project, run_dir / "lean", data_dir=a.data_dir)
    record = runs.finalize_run(run_dir, ok=code == 0)
    print(json.dumps({"run_dir": str(run_dir), "status": record["status"], "exit_code": code,
                      "reproducible": record["reproducible"],
                      "statistics": record["metrics"].get("statistics", {})}, indent=2))
    return 0 if record["status"] == "completed" else 1


def cmd_risk_check(a) -> int:
    """Dry-run only: evaluate one request against the gate, journal it if approved. Sends nothing."""
    limits = risk.RiskLimits.load(a.limits)
    req = json.loads(Path(a.request).read_text())
    journal = Path(a.journal)
    seen = {json.loads(l)["intent"]["client_order_id"] for l in journal.read_text().splitlines()} if journal.exists() else set()
    q, acc = req.get("quote"), req.get("account")
    decision = risk.evaluate(
        intent=OrderIntent(**req["intent"]) if req.get("intent") else None,
        limits=limits,
        spec=SymbolSpec(**req["symbol_spec"]) if req.get("symbol_spec") else None,
        quote=Quote(**(q | {"time": _ts(q["time"])})) if q else None,
        account=AccountSnapshot(**(acc | {"time": _ts(acc["time"])})) if acc else None,
        now=_ts(req["now"]) if req.get("now") else datetime.now(timezone.utc),
        seen_client_order_ids=seen,
        kill_switch_active=risk.kill_switch_active(limits),
    )
    if decision.approved:
        DryRunExecution(journal).submit(OrderIntent(**req["intent"]), decision)
    print(json.dumps(decision.to_dict(), indent=2))
    return 0 if decision.approved else 2


def cmd_import_csv(a) -> int:
    cfg = tomllib.loads(Path(a.config).read_text())
    src, spec = cfg["source"], SymbolSpec(**cfg["symbol_spec"])
    adapter = CsvBarsAdapter(Path(src["path"]), src["id"], src["timezone"], src["time_format"],
                             src["price_side"], spec, src.get("columns"))
    res = Resolution(cfg["resolution"])
    start, end = _ts(cfg["start"]).astimezone(timezone.utc), _ts(cfg["end"]).astimezone(timezone.utc)
    table = adapter.fetch_bars(spec.symbol, res, start, end)
    by_day = defaultdict(list)
    for row in table.to_pylist():
        by_day[row["time"].date()].append(row)
    written, skipped = [], []
    for day, rows in sorted(by_day.items()):
        try:
            p = rawstore.write_partition(a.root, "bars", adapter.source, spec.symbol, res, day,
                                         pa.Table.from_pylist(rows, schema=table.schema),
                                         {"name": type(adapter).__name__, "version": adapter.adapter_version},
                                         adapter.provenance(), supersede=a.supersede)
            written.append(str(p))
        except rawstore.PartitionExists:
            skipped.append(day.isoformat())
    print(json.dumps({"written": written, "skipped_existing": skipped}, indent=2))
    return 0


def cmd_verify(a) -> int:
    problems = rawstore.verify(a.root)
    print("\n".join(problems) or "ok")
    return 1 if problems else 0


def cmd_quality(a) -> int:
    from datetime import date
    rep = quality.report(a.root, a.source, a.symbol, Resolution(a.resolution), date.fromisoformat(a.start),
                         date.fromisoformat(a.end), a.week_open, a.week_close, a.session_tz)
    print(json.dumps(rep, indent=2) if a.json else quality.to_markdown(rep))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fxra")
    sub = p.add_subparsers(required=True)

    b = sub.add_parser("backtest", help="record a run and execute it in the pinned LEAN image")
    b.add_argument("config", type=Path)
    b.add_argument("--runs-root", type=Path, default=Path("runs"))
    b.add_argument("--data-dir", type=Path, help="LEAN-format data folder; default: sample data in image")
    b.set_defaults(fn=cmd_backtest)

    r = sub.add_parser("risk-check", help="dry-run one order intent through the risk gate")
    r.add_argument("--limits", type=Path, required=True)
    r.add_argument("--request", type=Path, required=True)
    r.add_argument("--journal", type=Path, default=Path("artifacts/dry-run/journal.jsonl"))
    r.set_defaults(fn=cmd_risk_check)

    i = sub.add_parser("import-csv", help="import a bar CSV into the immutable raw store")
    i.add_argument("config", type=Path)
    i.add_argument("--root", type=Path, default=Path("data/raw"))
    i.add_argument("--supersede", metavar="REASON", help="write a corrected batch for days that already exist")
    i.set_defaults(fn=cmd_import_csv)

    v = sub.add_parser("verify", help="recompute checksums for every raw partition")
    v.add_argument("--root", type=Path, default=Path("data/raw"))
    v.set_defaults(fn=cmd_verify)

    q = sub.add_parser("quality", help="gap and spread report for raw bars (read only)")
    q.add_argument("--source", required=True)
    q.add_argument("--symbol", required=True)
    q.add_argument("--resolution", choices=["minute", "hour"], required=True)
    q.add_argument("--start", required=True, help="YYYY-MM-DD (UTC day)")
    q.add_argument("--end", required=True, help="YYYY-MM-DD (UTC day, inclusive)")
    q.add_argument("--root", type=Path, default=Path("data/raw"))
    q.add_argument("--week-open", default="Sun 17:00")
    q.add_argument("--week-close", default="Fri 17:00")
    q.add_argument("--session-tz", default="America/New_York")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_quality)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
