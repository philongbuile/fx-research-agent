"""fxra command line. Run `uv run fxra --help`."""

import argparse
import json
import sys
import tomllib
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa

from . import lean, lean_export, quality, rawstore, report, risk, runs
from .adapters import CsvBarsAdapter, DryRunExecution
from .contracts import AccountSnapshot, OrderIntent, Quote, Resolution, SymbolSpec


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _execute(a, *, kind=None, window, stress=None, group_id=None) -> dict:
    """Record one run, run it in LEAN with its dates (and stress values) as parameters, finalize it."""
    export = a.data_dir / lean_export.EXPORT_JSON if a.data_dir else None
    run_dir = runs.new_run(a.config, a.runs_root, lean.image_ref(), kind=kind, window=window, stress=stress,
                           group_id=group_id, data_export=export if export and export.exists() else None)
    record = json.loads((run_dir / "run.json").read_text())
    project = runs.REPO / Path(record["code"]["strategy_path"]).parent
    params = {"start": window["start"], "end": window["end"], **(stress or {})}
    code = lean.backtest(project, run_dir / "lean", data_dir=a.data_dir, parameters=params)
    record = runs.finalize_run(run_dir, ok=code == 0)
    return {"run_dir": str(run_dir), "run_id": record["run_id"], "status": record["status"], "exit_code": code,
            "reproducible": record["reproducible"], "window": window,
            "statistics": record["metrics"].get("statistics", {})}


def _print(results: list[dict]) -> int:
    print(json.dumps(results, indent=2))
    return 0 if all(r["status"] == "completed" for r in results) else 1


def _segment_window(cfg: dict, segment: str) -> dict:
    rng = cfg["split"].get(segment)
    if not rng:
        raise SystemExit(f"error: [split] has no {segment} range")
    return {"segment": segment, "start": rng["start"], "end": rng["end"]}


def _guard_oos(a, cfg: dict) -> None:
    prior = runs.completed_oos_run(a.runs_root, cfg["run"]["hypothesis_id"])
    if prior:
        raise SystemExit(f"error: {cfg['run']['hypothesis_id']} already has a completed out-of-sample run "
                         f"({prior}). One OOS look per hypothesis; a changed design needs a new hypothesis id.")


def cmd_backtest(a) -> int:
    cfg = runs.load_config(a.config)
    window = _segment_window(cfg, a.segment)
    if a.segment == "out_of_sample":
        _guard_oos(a, cfg)
    return _print([_execute(a, window=window)])


def cmd_walk_forward(a) -> int:
    cfg = runs.load_config(a.config)
    wf = cfg.get("walk_forward")
    if not wf:
        raise SystemExit("error: config has no [walk_forward] table")
    code = runs.git_info()
    if not code["git_commit"] or code["git_dirty"]:
        raise SystemExit("error: code is dirty or not in git; walk-forward results are OOS evidence and must be reproducible")
    windows = runs.walk_forward_windows(date.fromisoformat(wf["start"]), date.fromisoformat(wf["end"]),
                                        wf["train_days"], wf["test_days"], wf["step_days"])
    if not windows:
        raise SystemExit("error: no complete window fits between start and end")
    _guard_oos(a, cfg)  # same one-look rule as a plain OOS run
    group_id = f"wf-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    return _print([_execute(a, kind="walk_forward", group_id=group_id,
                            window={"segment": "out_of_sample", **w["test"]}) for w in windows])


def cmd_stress(a) -> int:
    cfg = runs.load_config(a.config)
    window = _segment_window(cfg, "in_sample")  # robustness runs never touch OOS data
    variants = [(m, a.slippage_points) for m in (a.spread or [1.0])]
    if variants == [(1.0, 0.0)]:
        raise SystemExit("error: give --spread and/or --slippage-points")
    return _print([_execute(a, kind="stress", window=window,
                            stress={"spread_multiplier": m, "slippage_points": sp}) for m, sp in variants])


def cmd_report(a) -> int:
    print(report.render(report.load_records(a.runs_root), a.hypothesis))
    return 0


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


def cmd_export_lean(a) -> int:
    entry = lean_export.export(a.root, a.out, a.source, a.symbol, Resolution(a.resolution),
                               date.fromisoformat(a.start), date.fromisoformat(a.end), a.fixed_spread_points)
    print(json.dumps({k: entry[k] for k in ("rows", "price_side", "spread", "lean")}, indent=2))
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


def cmd_simulation_demo(a) -> int:
    from .simulation import Policy
    from .simulation_demo import run_demo
    from .simulation_memory import HindsightDocuments

    result = run_demo(a.db, Policy.load(a.config), HindsightDocuments.from_env() if a.hindsight else None)
    print(json.dumps(result, indent=2))
    return 0


def cmd_simulation_recover(a) -> int:
    from .simulation import Simulation

    engine = Simulation.open_existing(a.db)
    try:
        engine.recover()
    finally:
        engine.close()
    print('recovery tick completed')
    return 0


def _demo_agent(a):
    from .dashboard import Agent
    from .demo_agent import SubprocessBroker
    from .trade_journal import Journal

    import os

    from .lesson_memory import HindsightLessons

    broker = SubprocessBroker(a.mt5_python, a.terminal)
    # Lessons go to Hindsight as soon as a trade closes when the host provides a key (never logged).
    memory = HindsightLessons.from_env() if os.environ.get("HINDSIGHT_API_KEY") else None
    return Agent(Journal(a.journal), broker, a.limits, send=not a.no_send, interval=a.interval, roster_path=a.duty,
                 memory=memory, calendar_path=a.calendar)


def _on_duty(journal_path: Path, roster: Path) -> dict:
    from . import duty
    from .trade_journal import Journal

    return duty.on_duty(duty.load_roster(roster), datetime.now(timezone.utc), Journal(journal_path).latest_override())


SKILL_SOURCE, SKILL_MIRROR = Path(".claude/skills"), Path(".agents/skills")


def cmd_sync_skills(a) -> int:
    """Mirror Claude skills to `.agents/skills` so Codex reads the same instructions."""
    import shutil

    copied = []
    for skill in sorted(p for p in SKILL_SOURCE.iterdir() if (p / "SKILL.md").exists()):
        dest = SKILL_MIRROR / skill.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(skill, dest)
        copied.append(skill.name)
    print(json.dumps({"synced": copied, "to": str(SKILL_MIRROR)}))
    return 0


def cmd_demo_shift(a) -> int:
    """Duty rotation: who is on, take over, and log shift start/end summaries."""
    from datetime import timedelta

    from .trade_journal import Journal

    j, now = Journal(a.journal), datetime.now(timezone.utc)
    if a.action == "take":
        if not (a.agent and a.model and a.reason):
            raise SystemExit("take needs --agent, --model and --reason")
        j.take_duty(now, a.agent, a.model, now + timedelta(hours=a.hours), a.reason)
    elif a.action in ("start", "end"):
        on = _on_duty(a.journal, a.duty)
        j.log_shift(now, a.agent or on["agent"], a.model or on["model"], a.action, a.summary or "")
    print(json.dumps({"on_duty": _on_duty(a.journal, a.duty), "scoreboard": j.model_scoreboard()},
                     indent=2, default=str))
    return 0


def cmd_demo_step(a) -> int:
    print(json.dumps(_demo_agent(a).tick(), indent=2, default=str))
    return 0


def cmd_demo_policy(a) -> int:
    """Show the H-002 arm policies, or change your own arm's policy (D-31, D-33)."""
    from . import h002
    from .trade_journal import Journal

    j, demo = Journal(a.journal), a.journal.parent
    if not (a.set or a.enable or a.disable):
        print(json.dumps({"policies": {arm: h002.arm_policy(demo, arm, j) for arm in h002.ARMS},
                          "learning": j.learning_stats()}, indent=2))
        return 0
    if not (a.arm and a.reason):
        raise SystemExit("--arm and --reason are required when changing a policy")
    changes = {}
    for item in a.set or ():
        key, _, value = item.partition("=")
        try:
            changes[key] = json.loads(value)
        except ValueError:  # Windows PowerShell 5.1 strips inner quotes; plain words stay strings
            changes[key] = value
    if a.enable or a.disable:
        enabled = h002.arm_policy(demo, a.arm, j)["enabled"]
        changes["enabled"] = [p for p in h002.PATTERNS if (p in enabled or p in (a.enable or ())) and p not in (a.disable or ())]
    print(json.dumps(h002.edit_arm_policy(demo, a.arm, j, changes, a.author, a.reason), indent=2))
    return 0


def cmd_demo_propose(a) -> int:
    """Propose a distilled rule or a cross-arm idea; the other agent must review it."""
    from .trade_journal import Journal

    j, now = Journal(a.journal), datetime.now(timezone.utc)
    if a.withdraw is not None:
        print(json.dumps(j.withdraw(a.withdraw, now, a.author, a.note or ""), indent=2))
    elif a.review is not None:
        if a.agree == a.reject:
            raise SystemExit("review needs exactly one of --agree / --reject")
        print(json.dumps(j.review(a.review, now, a.author, a.agree, a.note or ""), indent=2))
    elif a.text:
        if not a.evidence:
            raise SystemExit("--evidence is required: cite the stats (arm, pattern, session, n)")
        print(json.dumps({"proposal_id": j.propose(now, a.author, a.text, a.evidence)}))
    else:
        print(json.dumps(j.state()["proposals"], indent=2))
    return 0


def cmd_demo_decide(a) -> int:
    """H-003 (D-42): show pending candidates, or take/skip one with a reason. The agent loop sends approved ones."""
    from . import h003
    from .trade_journal import Journal

    j, now = Journal(a.journal), datetime.now(timezone.utc)
    agent = a.author.split("/")[0]
    a.arm = a.arm or (agent if agent in h003.ARMS else "claude")  # your own arm by default
    if a.take or a.skip or a.skip_rest:
        if not a.reason:
            raise SystemExit("--reason is required: why take or skip this candidate")
        if a.skip_rest:
            ids = [c["id"] for c in h003.brief(j, now, a.arm)["pending"]]
        else:
            ids = [a.take or a.skip]
        out = []
        for cid in ids:
            try:
                c = h003.decide(j, cid, bool(a.take), a.reason, a.author, now, a.sl_atr, a.tp_rr)
                out.append({"id": cid, "status": c["status"], "sl_atr": c["sl_atr"], "tp_rr": c["tp_rr"]})
            except (ValueError, PermissionError) as e:
                out.append({"id": cid, "error": str(e)})
        print(json.dumps(out, indent=2))
        return 1 if any("error" in o for o in out) else 0
    print(json.dumps(h003.brief(j, now, a.arm), indent=2, default=str))
    return 0


def cmd_demo_playbook(a) -> int:
    """List playbook rules with recounted evidence, add a candidate rule, or change a rule's status."""
    from . import playbook
    from .trade_journal import Journal

    j, now = Journal(a.journal), datetime.now(timezone.utc)
    if a.add:
        try:
            rid = playbook.add(j, now, a.author, a.add, a.action, a.when, a.why or "", a.confirm_n)
        except ValueError as e:
            raise SystemExit(str(e))
        print(json.dumps({"rule_id": rid}))
    elif a.status:
        rid, status = a.status
        if status not in playbook.STATUSES:
            raise SystemExit(f"status must be one of {playbook.STATUSES}")
        j.set_rule_status(int(rid), now, status, f"{a.author}: {a.note or ''}".strip(), a.proposal)
        print(json.dumps({"rule_id": int(rid), "status": status}))
    else:
        print(json.dumps(playbook.report(j), indent=2, default=str))
    return 0


def cmd_demo_calendar(a) -> int:
    """Economic calendar (D-40): refresh from the MT5 export, then list recent and upcoming events."""
    from datetime import timedelta

    from . import econ_calendar
    from .trade_journal import Journal

    j, now = Journal(a.journal), datetime.now(timezone.utc)
    status = econ_calendar.refresh(j, a.calendar or econ_calendar.DEFAULT_PATH, now)
    events = j.events(now - timedelta(hours=a.back), now + timedelta(hours=a.hours))
    print(json.dumps({"feed": status, "events": [e for e in events if e["importance"] >= a.min_importance]}, indent=2))
    return 0


def cmd_demo_test_order(a) -> int:
    from .demo_agent import test_round_trip

    agent = _demo_agent(a)
    with agent.lock:
        result = test_round_trip(agent.journal, agent.broker, agent.limits(), datetime.now(timezone.utc),
                                 hold_seconds=a.hold_seconds)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["action"] == "round_trip" else 2


def cmd_demo_run(a) -> int:
    from .dashboard import serve

    serve(_demo_agent(a), a.port, loop=not a.no_loop)
    return 0


def cmd_demo_lesson(a) -> int:
    """Lessons from Claude/Codex reflection, and bookkeeping of Hindsight retention."""
    from .trade_journal import Journal

    j = Journal(a.journal)
    if a.add:
        lesson = j.add_lesson(datetime.now(timezone.utc), a.trade, a.author, a.add, a.hindsight_document)
        print(json.dumps({"lesson_id": lesson}))
    elif a.mark_retained:
        j.mark_retained(int(a.mark_retained[0]), a.mark_retained[1])
        print(json.dumps({"lesson_id": a.mark_retained[0], "hindsight_document": a.mark_retained[1]}))
    else:
        state = j.state()
        pending = [l for l in state["lessons"] if not l["hindsight_document"]]
        print(json.dumps({"summary": state["summary"], "trades": state["trades"][:20],
                          "lessons_not_in_hindsight": pending, "recent_decisions":
                          [{k: d[k] for k in ("bar", "action", "side", "reason")} for d in state["decisions"][:24]]},
                         indent=2, default=str))
    return 0


def cmd_demo_insights(a) -> int:
    """Print the H-002 results digest, or retain changed digest documents to Hindsight."""
    import os

    from . import lesson_insights
    from .lesson_memory import HindsightLessons
    from .trade_journal import Journal

    j = Journal(a.journal)
    if not a.retain:
        print("\n\n".join(f"[{d['document']}]\n{d['content']}" for d in lesson_insights.digest(j)) or "no closed trades")
        return 0
    if not os.environ.get("HINDSIGHT_API_KEY"):
        raise SystemExit("--retain needs HINDSIGHT_API_KEY in the host environment")
    print(json.dumps(lesson_insights.sync(j, HindsightLessons.from_env(), datetime.now(timezone.utc)), indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fxra")
    sub = p.add_subparsers(required=True)

    def demo_args(sp):
        sp.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
        sp.add_argument("--limits", type=Path, default=Path("config/risk.toml"))
        sp.add_argument("--mt5-python", type=Path, default=Path("artifacts/mt5-mcp-runtime/Scripts/python.exe"))
        sp.add_argument("--terminal", type=Path, default=Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"))
        sp.add_argument("--no-send", action="store_true", help="decide and journal, but never send orders")
        sp.add_argument("--interval", type=int, default=30, help="seconds between agent checks")
        sp.add_argument("--duty", type=Path, default=Path("config/duty.toml"), help="duty roster (default schedule if absent)")
        sp.add_argument("--calendar", type=Path, default=None, help="MT5 calendar export (default: terminal Common\\Files)")

    ss = sub.add_parser("sync-skills", help="mirror .claude/skills to .agents/skills for Codex")
    ss.set_defaults(fn=cmd_sync_skills)

    sh = sub.add_parser("demo-shift", help="duty rotation: status, take over, log shift start/end")
    sh.add_argument("action", choices=["status", "take", "start", "end"])
    sh.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
    sh.add_argument("--duty", type=Path, default=Path("config/duty.toml"))
    sh.add_argument("--agent")
    sh.add_argument("--model")
    sh.add_argument("--hours", type=float, default=4.0)
    sh.add_argument("--reason")
    sh.add_argument("--summary")
    sh.set_defaults(fn=cmd_demo_shift)

    ds = sub.add_parser("demo-step", help="one H-001B decision cycle on the MT5 demo account (D-30)")
    demo_args(ds)
    ds.set_defaults(fn=cmd_demo_step)

    dp = sub.add_parser("demo-policy", help="show H-002 arm policies, or change your own arm's policy")
    dp.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
    dp.add_argument("--arm", choices=["claude", "codex"])
    dp.add_argument("--set", action="append", metavar="KEY=JSON", help="e.g. tp_rr=2 or max_hold_bars=12")
    dp.add_argument("--enable", action="append", metavar="PATTERN")
    dp.add_argument("--disable", action="append", metavar="PATTERN")
    dp.add_argument("--reason")
    dp.add_argument("--author", required=False, default="", help="<agent>/<model>, must own the arm")
    dp.set_defaults(fn=cmd_demo_policy)

    pr = sub.add_parser("demo-propose", help="list proposals, propose a rule, or review the other agent's proposal")
    pr.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
    pr.add_argument("--text")
    pr.add_argument("--evidence")
    pr.add_argument("--review", type=int, metavar="ID")
    pr.add_argument("--withdraw", type=int, metavar="ID", help="take back your own open proposal (with --note)")
    pr.add_argument("--agree", action="store_true")
    pr.add_argument("--reject", action="store_true")
    pr.add_argument("--note")
    pr.add_argument("--author", default="", help="<agent>/<model>")
    pr.set_defaults(fn=cmd_demo_propose)

    dd = sub.add_parser("demo-decide", help="H-003: list pending candidates, or take/skip one with a reason (D-42)")
    dd.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
    dd.add_argument("--arm", choices=["claude", "codex"], help="H-003 arm to list (default: the --author's own arm)")
    dd.add_argument("--take", metavar="ID")
    dd.add_argument("--skip", metavar="ID")
    dd.add_argument("--skip-rest", action="store_true", help="skip every pending candidate with one reason")
    dd.add_argument("--sl-atr", type=float, help="stop = max(this x ATR, min pips); default and bounds in the listing")
    dd.add_argument("--tp-rr", type=float, help="target in R; default and bounds in the listing")
    dd.add_argument("--reason")
    dd.add_argument("--author", default="", help="<agent>/<model>, must own the arm")
    dd.set_defaults(fn=cmd_demo_decide)

    pb = sub.add_parser("demo-playbook", help="playbook rules distilled from lessons, with evidence from the journal")
    pb.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
    pb.add_argument("--add", metavar="TITLE", help="add a candidate rule")
    pb.add_argument("--action", choices=["avoid", "prefer", "watch"], default="watch")
    pb.add_argument("--when", action="append", metavar="FIELD=V1,V2",
                    help="trade filter, e.g. pattern=breakout10,breakout20 or hour=18 (repeat for AND)")
    pb.add_argument("--why", help="the lesson or numbers behind the rule")
    pb.add_argument("--confirm-n", type=int, default=30, help="matched trades needed before proposing")
    pb.add_argument("--status", nargs=2, metavar=("ID", "STATUS"), help="candidate|proposed|adopted|retired")
    pb.add_argument("--proposal", type=int, help="demo-propose id when status becomes proposed")
    pb.add_argument("--note")
    pb.add_argument("--author", default="", help="<agent>/<model>")
    pb.set_defaults(fn=cmd_demo_playbook)

    dc = sub.add_parser("demo-calendar", help="economic calendar from the MT5 export: recent and upcoming events")
    dc.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
    dc.add_argument("--calendar", type=Path, default=None)
    dc.add_argument("--hours", type=float, default=24, help="look ahead")
    dc.add_argument("--back", type=float, default=6, help="look back (actual vs forecast)")
    dc.add_argument("--min-importance", type=int, default=2, help="2 moderate, 3 high")
    dc.set_defaults(fn=cmd_demo_calendar)

    dt = sub.add_parser("demo-test-order", help="TECH-0003: one risk-gated 0.01-lot buy/close round trip")
    demo_args(dt)
    dt.add_argument("--hold-seconds", type=int, default=60)
    dt.set_defaults(fn=cmd_demo_test_order)

    dr = sub.add_parser("demo-run", help="agent loop + local dashboard on 127.0.0.1")
    demo_args(dr)
    dr.add_argument("--port", type=int, default=8765)
    dr.add_argument("--no-loop", action="store_true", help="dashboard only, no agent steps")
    dr.set_defaults(fn=cmd_demo_run)

    dl = sub.add_parser("demo-lesson", help="list trades/lessons for reflection, add a lesson, or mark one retained")
    dl.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
    dl.add_argument("--add", metavar="TEXT")
    dl.add_argument("--trade", type=int, help="position id the lesson is about")
    dl.add_argument("--author", default="claude")
    dl.add_argument("--hindsight-document")
    dl.add_argument("--mark-retained", nargs=2, metavar=("LESSON_ID", "DOCUMENT"), type=str)
    dl.set_defaults(fn=cmd_demo_lesson)

    di = sub.add_parser("demo-insights", help="H-002 results digest by pattern, session, hour and regime")
    di.add_argument("--journal", type=Path, default=Path("artifacts/demo/journal.sqlite"))
    di.add_argument("--retain", action="store_true", help="retain changed digest documents to Hindsight")
    di.set_defaults(fn=cmd_demo_insights)

    sd = sub.add_parser('simulation-demo', help='synthetic lifecycle demo; no trading or market results')
    sd.add_argument('--config', type=Path, default=Path('config/simulation-demo.example.toml'))
    sd.add_argument('--db', type=Path, default=Path('artifacts/simulation/demo.sqlite'))
    sd.add_argument('--hindsight', action='store_true', help='use HINDSIGHT_URL and HINDSIGHT_API_KEY from host environment')
    sd.set_defaults(fn=cmd_simulation_demo)

    sr = sub.add_parser('simulation-recover', help='expire interrupted final sessions without waiting for an agent')
    sr.add_argument('--db', type=Path, required=True)
    sr.set_defaults(fn=cmd_simulation_recover)

    def run_args(sp):
        sp.add_argument("config", type=Path)
        sp.add_argument("--runs-root", type=Path, default=Path("runs"))
        sp.add_argument("--data-dir", type=Path, help="LEAN-format data folder; default: sample data in image")

    b = sub.add_parser("backtest", help="record a run and execute it in the pinned LEAN image")
    run_args(b)
    b.add_argument("--segment", choices=["in_sample", "out_of_sample"], default="in_sample",
                   help="which [split] range to backtest; out_of_sample is allowed once per hypothesis")
    b.set_defaults(fn=cmd_backtest)

    w = sub.add_parser("walk-forward", help="one OOS run per [walk_forward] test window, frozen rules, clean code only")
    run_args(w)
    w.set_defaults(fn=cmd_walk_forward)

    st = sub.add_parser("stress", help="in-sample cost stress runs (kind=stress), one per spread multiplier")
    run_args(st)
    st.add_argument("--spread", type=float, action="append", help="spread multiplier, repeatable (e.g. 1.5)")
    st.add_argument("--slippage-points", type=float, default=0.0, help="extra slippage in price points")
    st.set_defaults(fn=cmd_stress)

    rp = sub.add_parser("report", help="markdown comparison of run records, to stdout")
    rp.add_argument("--runs-root", type=Path, default=Path("runs"))
    rp.add_argument("--hypothesis")
    rp.set_defaults(fn=cmd_report)

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

    e = sub.add_parser("export-lean", help="export raw-store bars to LEAN QuoteBar files (forex/oanda layout)")
    e.add_argument("--source", required=True)
    e.add_argument("--symbol", required=True)
    e.add_argument("--resolution", required=True, choices=["minute", "hour"])
    e.add_argument("--start", required=True, help="first UTC day, YYYY-MM-DD")
    e.add_argument("--end", required=True, help="last UTC day (inclusive), YYYY-MM-DD")
    e.add_argument("--root", type=Path, default=Path("data/raw"))
    e.add_argument("--out", type=Path, default=Path("data/lean"))
    e.add_argument("--fixed-spread-points", type=int, metavar="N",
                   help="spread for bars whose spread_points is null; without it such bars are refused")
    e.set_defaults(fn=cmd_export_lean)

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
