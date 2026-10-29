"""Local dashboard + agent loop for the demo agent. Stdlib only, binds 127.0.0.1.

GET /            single page (auto-refresh)
GET /api/state   journal read model + latest live snapshot + kill switch status
GET /api/decision-log?symbol=&strategy=&version=&decision=&decider=&since=&until=&limit=   decision traces (D-44)
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import decision_log, demo_agent, duty, econ_calendar, h002, h003, lesson_insights, lesson_memory, playbook, risk
from .trade_journal import Journal


class CachingBroker:
    """Keeps the latest snapshot for the page; passes everything else through."""

    def __init__(self, broker):
        self.broker, self.last, self.error = broker, None, None

    def snapshot(self, payload):
        try:
            self.last, self.error = self.broker.snapshot(payload), None
        except Exception as e:
            self.error = str(e)
            raise
        return self.last

    def send(self, payload):
        return self.broker.send(payload)

    def close(self, payload):
        return self.broker.close(payload)


class Agent:
    def __init__(self, journal: Journal, broker, limits_path: Path, *, send=True, interval=30,
                 roster_path: Path | None = None, memory=None, calendar_path: Path | None = None):
        self.journal, self.broker = journal, CachingBroker(broker)
        self.limits_path, self.send, self.interval = Path(limits_path), send, interval
        self.roster_path = roster_path
        self.memory = memory  # HindsightLessons, or None when no Hindsight key is configured
        self.lock = threading.Lock()
        self.last_step = None
        self.last_memory = {"enabled": memory is not None}
        self.last_insights = None
        self.calendar_path = Path(calendar_path) if calendar_path else econ_calendar.DEFAULT_PATH
        self.calendar = {"status": "missing"}
        self.last_trace = None

    def refresh_calendar(self, now):
        """Economic calendar (D-40) for this tick; trading continues without it."""
        try:
            self.calendar = econ_calendar.refresh(self.journal, self.calendar_path, now)
        except Exception as e:
            self.calendar = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
        return self.journal.events(now - timedelta(hours=3), now + timedelta(hours=3))

    def sync_traces(self, now=None, snap=None):
        """Decision log (D-44), observation only: runs after the strategies and never touches trading."""
        try:
            self.last_trace = decision_log.sync(self.journal, now or datetime.now(timezone.utc), snap)
        except Exception as e:
            self.last_trace = {"error": f"{type(e).__name__}: {e}"}
        return self.last_trace

    def decision_log(self, params: dict) -> dict:
        args = {k: params[k] for k in decision_log.FILTERS if params.get(k)}
        with self.lock:
            self.sync_traces()
            out = decision_log.query(self.journal, **args, limit=min(int(params.get("limit") or 100), 500),
                                     offset=int(params.get("offset") or 0))
        return {**out, "sync": self.last_trace}

    def sync_lessons(self):
        if self.memory is None:
            return self.last_memory
        try:
            self.last_memory = {"enabled": True, **lesson_memory.flush(self.journal, self.memory, self.lock)}
        except Exception as e:
            self.last_memory = {"enabled": True, "error": type(e).__name__}
        return self.last_memory

    def sync_insights(self, now=None, every_seconds=3600):
        """Hourly digest of demo results to Hindsight (lesson_insights); unchanged documents are skipped."""
        now = now or datetime.now(timezone.utc)
        if self.memory is None or (self.last_insights and
                                   (now - self.last_insights["checked"]).total_seconds() < every_seconds):
            return self.last_insights
        try:
            self.last_insights = {"checked": now, **lesson_insights.sync(self.journal, self.memory, now, self.lock)}
        except Exception as e:
            self.last_insights = {"checked": now, "error": type(e).__name__}
        return self.last_insights

    def arm_labels(self) -> dict:
        """Arm name -> label of the model its owner runs now (config/duty.toml [agents])."""
        roster = duty.load_roster(self.roster_path)
        return {cfg["arm"]: duty.agent_info(roster, cfg["owner"])["label"]
                for cfg in (*h002.ARMS.values(), *h003.ARMS.values())}

    def model_labels(self) -> dict:
        """Model id (and agent name) -> display label, for attribution columns written as ids."""
        roster = duty.load_roster(self.roster_path)
        out = {"gpt-6-sol": duty.agent_info(roster, "codex")["label"]}  # id used before D-35
        for agent in roster.get("agents", {}):
            info = duty.agent_info(roster, agent)
            out[info["model"]] = out[agent] = info["label"]
        return out

    def duty(self, now=None):
        return duty.on_duty(duty.load_roster(self.roster_path), now or datetime.now(timezone.utc),
                            self.journal.latest_override())

    def limits(self):
        try:
            return risk.RiskLimits.load(self.limits_path)
        except (OSError, KeyError, ValueError):
            return None  # missing config = kill switch active (fail closed)

    @property
    def demo_dir(self):
        return self.journal.path.parent

    def tick(self):
        now = datetime.now(timezone.utc)
        with self.lock:
            limits = self.limits()
            try:
                snap = self.broker.snapshot(demo_agent.SNAPSHOT_REQUEST)
            except Exception as e:
                self.last_step = {"at": now.isoformat(), "action": "error", "reason": str(e)}
                return self.last_step
            # The snapshot subprocess takes seconds: a clock taken before it makes the
            # fresh quote look like it comes from the future (risk gate: quote_from_future).
            now = datetime.now(timezone.utc)
            snap = {**snap, "events": self.refresh_calendar(now)}
            results, on = {}, self.duty(now)
            runs = [("H-001B", lambda: demo_agent.step(self.journal, self.broker, limits, now,
                                                       send=self.send, snap=snap, duty=on))]
            labels = self.arm_labels()
            for arm, cfg in h002.ARMS.items():
                runs.append((cfg["arm"], lambda arm=arm, cfg=cfg: h002.step(
                    self.journal, self.broker, limits, now, snap,
                    h002.arm_policy(self.demo_dir, arm, self.journal), send=self.send, duty=on, arm=arm,
                    arm_model=labels[cfg["arm"]])))
            for arm, cfg in h003.ARMS.items():  # D-42: sends only what the arm's model approved
                runs.append((cfg["arm"], lambda arm=arm, cfg=cfg: h003.step(
                    self.journal, self.broker, limits, now, snap, h003.policy(self.demo_dir, arm, self.journal),
                    send=self.send, duty=on, arm=arm, arm_model=labels[cfg["arm"]])))
            for name, run in runs:
                try:
                    results[name] = run()
                except Exception as e:  # one strategy failing must not stop the others
                    results[name] = {"action": "error", "reason": f"{type(e).__name__}: {e}"}
            self.last_step = {"at": now.isoformat(), **results}
            self.sync_traces(now, snap)
        return self.last_step

    def run_forever(self, stop: threading.Event):
        while not stop.is_set():
            self.tick()
            self.sync_lessons()  # a lesson written this tick reaches Hindsight before the next bar
            self.sync_insights()
            stop.wait(self.interval)

    def state(self):
        limits = self.limits()
        with self.lock:
            data = self.journal.state()
            data["playbook"] = playbook.report(self.journal)
            data["h003"] = {arm: h003.brief(self.journal, datetime.now(timezone.utc), arm) for arm in h003.ARMS}
            data["h003_scoreboard"] = h003.scoreboard(self.journal)
        ids = [f"{demo_agent.STRATEGY_ID}@{demo_agent.STRATEGY_VERSION}", f"{h002.STRATEGY_ID}@{h002.STRATEGY_VERSION}",
               f"{h003.STRATEGY_ID}@{h003.STRATEGY_VERSION}"]
        data.update(live=self.broker.last, live_error=self.broker.error, last_step=self.last_step,
                    kill_switch=risk.kill_switch_active(limits), mode=limits.mode if limits else None,
                    strategies={i: bool(limits and i in limits.approved_strategies) for i in ids},
                    policies={arm: h002.arm_policy(self.demo_dir, arm, self.journal) for arm in h002.ARMS},
                    duty=self.duty(), arm_labels=self.arm_labels(), model_labels=self.model_labels(),
                    memory=self.last_memory, calendar=self.calendar,
                    events=self.journal.events(datetime.now(timezone.utc) - timedelta(hours=12),
                                               datetime.now(timezone.utc) + timedelta(hours=24)),
                    server_time=datetime.now(timezone.utc).isoformat())
        return data


def make_handler(agent: Agent):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if self.path == "/api/state":
                self._send(200, json.dumps(agent.state(), default=str).encode(), "application/json")
            elif url.path == "/api/decision-log":
                params = {k: v[-1] for k, v in parse_qs(url.query).items()}
                try:
                    body = agent.decision_log(params)
                except ValueError as e:  # a malformed filter
                    self._send(400, str(e).encode(), "text/plain")
                    return
                self._send(200, json.dumps(body, default=str).encode(), "application/json")
            elif self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args):
            pass

    return Handler


class _ExclusiveServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind the same port, so a second
    # demo-run would silently start a second trading loop. Bind exclusively instead.
    allow_reuse_address = False


def serve(agent: Agent, port: int, *, loop=True):
    stop = threading.Event()
    server = _ExclusiveServer(("127.0.0.1", port), make_handler(agent))  # fails if a loop already runs
    if loop:
        threading.Thread(target=agent.run_forever, args=(stop,), daemon=True).start()
    print(f"dashboard: http://127.0.0.1:{port}  (agent loop {'on' if loop else 'off'}, send={agent.send})", flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FX Demo Agent</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e4e4df;--up:#1a7f4b;--down:#c0392b;--accent:#2f5bd3}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecece8;--mute:#9a9a94;--line:#2e2e2b;--up:#4cc38a;--down:#ff6b5e;--accent:#7b9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,sans-serif}
header{padding:16px 20px;display:flex;flex-wrap:wrap;gap:12px;align-items:center;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0 12px 0 0}h2{font-size:14px;margin:0 0 10px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
.pill{padding:3px 10px;border-radius:99px;border:1px solid var(--line);font-size:12px}
.ok{color:var(--up)}.bad{color:var(--down)}
main{padding:16px 20px;display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.wide{grid-column:1/-1}.kpis{display:flex;flex-wrap:wrap;gap:18px}.kpi b{display:block;font-size:20px}
.kpi span{color:var(--mute);font-size:12px}table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mute);font-weight:500}
.scroll{overflow-x:auto;max-height:420px;overflow-y:auto}.hscroll{overflow-x:auto}.mono{font-family:ui-monospace,Consolas,monospace}
.lesson{border-left:3px solid var(--accent);padding:6px 10px;margin:0 0 10px}.lesson small{color:var(--mute)}
svg{width:100%;height:160px}.muted{color:var(--mute)}
.num{font-variant-numeric:tabular-nums;white-space:nowrap}.right{text-align:right}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line);white-space:nowrap}
.b-buy,.b-entry{color:var(--up);border-color:var(--up)}.b-sell,.b-exit,.b-blocked,.b-error{color:var(--down);border-color:var(--down)}
.b-hold{color:var(--accent);border-color:var(--accent)}
.track{position:relative;height:8px;min-width:120px;border-radius:99px;margin:6px 0 2px;
  background:linear-gradient(90deg,color-mix(in srgb,var(--down) 35%,transparent) 0 var(--e),color-mix(in srgb,var(--up) 35%,transparent) var(--e) 100%)}
.track i{position:absolute;top:-3px;width:14px;height:14px;margin-left:-7px;border-radius:50%;background:var(--ink);border:2px solid var(--card)}
.track b{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--mute)}
.ends{display:flex;justify-content:space-between;font-size:11px;color:var(--mute)}
.possum{display:flex;flex-wrap:wrap;gap:8px 18px;margin:0 0 10px}
.steps{display:grid;gap:8px}.steprow{display:grid;grid-template-columns:minmax(110px,auto) auto 1fr;gap:4px 12px;align-items:baseline;
  padding:8px 0;border-bottom:1px solid var(--line)}.steprow:last-child{border-bottom:0}
@media (max-width:640px){.steprow{grid-template-columns:1fr auto}.steprow .why{grid-column:1/-1}}
h3{font-size:13px;margin:16px 0 8px}h3:first-child{margin-top:0}
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr))}
.mcard{border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.mcard h4{margin:0;font-size:15px}.mcard .sub{color:var(--mute);font-size:12px;margin-bottom:6px}.big{font-size:24px;font-weight:600}
.mini{padding:8px 12px}.mini .big{font-size:18px}
.wl{display:flex;height:6px;border-radius:99px;overflow:hidden;background:var(--line);margin:8px 0 6px;min-width:60px}
.wl i{background:var(--up)}.wl u{background:var(--down)}
.stats{display:flex;flex-wrap:wrap;gap:2px 14px;font-size:12px;color:var(--mute)}.stats b{color:var(--ink);font-weight:600}
.cols{display:grid;gap:8px 20px;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr))}.cols>*{min-width:0}
table.mx{width:auto;min-width:100%}.mx th,.mx td{text-align:center;white-space:nowrap}.mx th:first-child,.mx td:first-child{text-align:left}.mx th{white-space:normal}
.cell{display:block;border-radius:6px;padding:3px 8px;font-size:12px;font-variant-numeric:tabular-nums}.cell small{display:block;color:var(--mute);font-size:10px}
.hours{display:grid;grid-template-columns:repeat(24,minmax(0,1fr));gap:3px}
@media (max-width:640px){.hours{grid-template-columns:repeat(12,minmax(0,1fr))}}
.hours div{border-radius:5px;padding:4px 0;text-align:center;font-size:10px;color:var(--mute);border:1px solid var(--line)}.hours div b{display:block;font-size:11px;color:var(--ink)}
.netbar{position:relative;height:6px;width:120px;background:var(--line);border-radius:99px;display:inline-block;vertical-align:middle;margin-right:8px}
.netbar i{position:absolute;top:0;bottom:0;border-radius:99px}.netbar:after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--mute)}
.chips{display:flex;flex-wrap:wrap;gap:4px}.chip{padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.chip.off{text-decoration:line-through;color:var(--mute)}
.params{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:10px 0}
.params div{border:1px solid var(--line);border-radius:8px;padding:6px 8px}.params span{display:block;color:var(--mute);font-size:11px}
.params .diff{border-color:var(--accent)}.params .diff span:after{content:" · differs";color:var(--accent)}
details{margin-top:12px}summary{cursor:pointer;color:var(--mute);font-size:13px}
.tl{display:grid;grid-template-columns:auto auto 1fr;gap:4px 12px;align-items:baseline;padding:6px 0;border-bottom:1px solid var(--line)}
.learnhead{border-style:dashed;margin-top:16px}
.tl:last-child{border-bottom:0}@media (max-width:640px){.tl{grid-template-columns:1fr auto}.tl>:last-child{grid-column:1/-1}}
.filters{display:flex;flex-wrap:wrap;gap:8px 12px;margin:0 0 10px}.filters label{display:flex;flex-direction:column;gap:2px;font-size:11px;color:var(--mute)}
.filters select,.filters input{font:inherit;font-size:13px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);max-width:190px}
tr.lrow{cursor:pointer}tr.lrow:hover,tr.lrow.open{background:color-mix(in srgb,var(--accent) 7%,transparent)}td.ldet{white-space:normal;padding:4px 0 12px}
.dgrid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr))}
.dbox{border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:0}.dbox h4{margin:0 0 2px;font-size:13px}.dbox h5{margin:8px 0 2px;font-size:12px;color:var(--mute);font-weight:500}
.dbox.seen{border-left:3px solid var(--accent)}.dbox.post{border-style:dashed}.hint{color:var(--mute);font-size:11px;margin:0 0 6px}
table.kv th{width:42%;font-size:11px;padding:2px 6px}table.kv td{font-size:12px;padding:2px 6px;word-break:break-word}
.rlist{margin:0;padding:0;list-style:none;font-size:12px}.rlist li{padding:2px 0}.rlist .chip{margin-right:6px}
.o-rule{border-color:var(--accent)}.o-model{border-color:var(--up);color:var(--up)}.o-owner{border-color:var(--down);color:var(--down)}.o-derived{border-style:dashed;color:var(--mute)}.o-outcome{border-style:dotted}
blockquote.stated{margin:6px 0;padding:6px 10px;border-left:3px solid var(--up);background:color-mix(in srgb,var(--up) 7%,transparent);font-size:13px}
.b-close{color:var(--down);border-color:var(--down)}.b-no_trade{color:var(--mute)}
</style></head><body>
<header><h1>FX Demo Agent</h1><span id="duty" class="pill"></span><span id="memsync" class="pill"></span><span id="calsync" class="pill"></span><span id="strategy" class="pill"></span><span id="mode" class="pill"></span>
<span id="kill" class="pill"></span><span id="conn" class="pill"></span><span id="updated" class="muted"></span></header>
<main>
<section class="card wide"><h2>Account &amp; market</h2><div class="kpis" id="kpis"></div></section>
<section class="card wide"><h2>H-003 · model vs model · the model decides each entry</h2><div id="h003"></div></section>
<section class="card wide"><h2>Decision log · inputs, reasons, decision, outcome</h2>
  <p class="muted" style="margin:0 0 10px">One row per evaluation or decision. Click a row: <b>1</b> what the decider was given, <b>2</b> the decision with the coded reasons and short reason it recorded,
  <b>3</b> codes fxra derived afterwards, <b>4</b> the outcome measured later. No chain-of-thought or hidden reasoning is collected. Observation only: the log never changes orders.</p>
  <div class="filters" id="dlog-filters"></div><div id="dlog-status" class="muted" style="font-size:12px"></div>
  <div class="hscroll"><table id="dlog"></table></div><div id="dlog-more"></div></section>
<section class="card wide"><h2>Daily KPI (UTC day)</h2><div id="kpi"></div></section>
<section class="card wide"><h2>Open positions</h2><div id="position"></div></section>
<section class="card wide"><h2>Agent status</h2><div id="step"></div></section>
<section class="card wide"><h2>Economic calendar (MT5, moderate and high importance)</h2><div id="calendar"></div></section>
<section class="card wide"><h2>Equity (whole demo account)</h2><div id="equity"></div></section>
<section class="card wide"><h2>Trades (broker deals)</h2><label class="muted" style="font-size:13px"><input type="checkbox" id="alltrades" onchange="ALLT=this.checked;load()"> include H-002 / H-001B learning data</label>
  <div class="scroll"><table id="trades"></table></div><div id="trades-more"></div></section>
<section class="card wide learnhead"><h2>Learning data · H-002 scripted control and history</h2><p class="muted" style="margin:0">Everything below comes from the scripted explorer (H-002, every matched pattern trades) and the history since 2026-10-28.
  It keeps running as the control for H-003 and is the evidence the models read before deciding: playbook, lessons, pattern stats. It is not the current way of trading.</p></section>
<section class="card wide"><h2>Decisions, bar by bar (H-001B / H-002 script)</h2><div class="scroll"><table id="decisions"></table></div><div id="decisions-more"></div></section>
<section class="card wide"><h2>H-002 scoreboard (scripted arms)</h2><div id="scoreboard"></div></section>
<section class="card wide"><h2>What H-002 is learning (exploration, after assumed cost)</h2><div id="learning"></div></section>
<section class="card wide"><h2>Playbook (candidate rules, evidence recounted from trades)</h2><div id="playbook"></div></section>
<section class="card wide"><h2>Proposals (need the other model's review)</h2><div id="proposals"></div></section>
<section class="card wide"><h2>H-002 arm policies</h2><div id="policy"></div></section>
<section class="card wide"><h2>Lessons</h2><div id="lessons"></div><div id="lessons-more"></div></section>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const money=(v,c="")=>v==null?"–":`<span class="${v>0?"ok":v<0?"bad":""}">${v>0?"+":""}${(+v).toFixed(2)}</span> ${c}`;
const t=s=>s?s.replace("T"," ").slice(0,16)+"Z":"–";
function kpi(label,val){return `<div class="kpi"><b>${val}</b><span>${label}</span></div>`}
function chart(rows){
  if(rows.length<2) return '<p class="muted">Not enough equity points yet.</p>';
  const v=rows.map(r=>r.equity),mn=Math.min(...v),mx=Math.max(...v),pad=(mx-mn)||1;
  const pts=v.map((y,i)=>`${(i/(v.length-1)*1000).toFixed(1)},${(150-(y-mn)/pad*140).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 1000 160" preserveAspectRatio="none"><polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts}"/></svg>
  <div class="muted">${t(rows[0].at)} → ${t(rows.at(-1).at)} · min ${mn.toFixed(2)} · max ${mx.toFixed(2)}</div>`}
async function load(){
  let s; try{ s=await (await fetch("/api/state")).json() }catch(e){ $("updated").textContent="dashboard offline"; return }
  const L=s.live||{}, a=L.account||{}, q=L.quote, ccy=a.currency||"";
  $("strategy").textContent=Object.entries(s.strategies).map(([k,v])=>k+(v?" ✓":" ✗ not approved")).join("  ·  ");
  const k=s.kpi, pct=Math.max(0,Math.min(100,k.progress*100));
  const h3today=s.trades.filter(r=>(r.entry_id||"").startsWith("H3")&&r.net!=null&&(r.close_time||"").slice(0,10)===k.day_utc).reduce((x,r)=>x+r.net,0);
  $("kpi").innerHTML=`<div class="kpis">${kpi("H-003 net today",money(h3today,"USD"))}${kpi("Whole account net today",money(k.net_today,"USD"))}${kpi("Target",k.target_usd+" USD")}${kpi("Progress (account)",(k.progress*100).toFixed(0)+"%")}</div>
    <div style="height:10px;background:var(--line);border-radius:99px;margin-top:10px"><div style="height:10px;width:${pct}%;background:${k.net_today<0?"var(--down)":"var(--up)"};border-radius:99px"></div></div>
    <p class="muted">Tracked, not optimised: the agent never sizes up to chase it.</p>`;
  const AL=s.arm_labels||{}, ML=s.model_labels||{}; LBL=ML;
  const armLabel=n=>AL[n]?`${AL[n]}`:n;  // H-002-claude -> "Claude Opus 5.5 Medium"
  const who=a=>{if(!a)return "";const [ag,m]=String(a).split("/");return ML[m]||ML[ag]||a};
  const d=s.duty; $("duty").innerHTML=`on duty: <b>${esc(d.label||d.model)}</b>`+(d.co_duty||[]).map(c=>` + <b>${esc(c.label||c.model)}</b> <span class="muted">until ${esc((c.until||"").slice(11,16))}</span>`).join("");
  const mem=s.memory||{}; $("memsync").innerHTML=!mem.enabled?'<span class="muted">Hindsight sync off</span>':mem.error||mem.failed?`<span class="bad">Hindsight sync failing${mem.pending?` · ${mem.pending} pending`:""}</span>`:`<span class="ok">Hindsight synced</span>${mem.pending?` · ${mem.pending} pending`:""}`;
  // shared pieces for the scoreboard, learning and policy cards
  const SESS=["asia","london","overlap_ny","late_ny"], pc=v=>(v*100).toFixed(0)+"%";
  const P=s.policies, arms=Object.keys(P), ls=s.learning, sb=s.scoreboard;
  const syms=[...new Set([...arms.flatMap(a=>P[a].symbols||[]),...Object.keys(ls.by_symbol||{})])];
  const allPat=[...new Set([...arms.flatMap(a=>P[a].enabled||[]),...Object.keys(ls.by_pattern||{})])];
  const stats=(g,small)=>`<div class="wl"><i style="flex:${g.wins}"></i><u style="flex:${g.losses}"></u><s style="flex:${g.n-g.wins-g.losses}"></s></div>
    <div class="stats"><span><b>${g.n}</b> trade${g.n==1?"":"s"}${g.n<small?" (small)":""}</span><span><b>${pc(g.win_rate)}</b> win · ${g.wins}W/${g.losses}L</span><span>PF <b>${g.profit_factor??"–"}</b></span><span><b>${money(g.avg_pips_after_cost)}</b> pips/trade</span></div>`;
  const maxAbs=o=>Math.max(1,...Object.values(o||{}).map(g=>Math.abs(g.net)));
  const tint=(v,mx)=>`background:color-mix(in srgb,var(${v>=0?"--up":"--down"}) ${Math.round(12+50*Math.min(1,Math.abs(v)/mx))}%,transparent)`;
  const heat=(g,mx)=>g?`<span class="cell" style="${tint(g.net,mx)}">${g.net>0?"+":""}${g.net.toFixed(0)}<small>${g.n} · ${g.wins}W/${g.losses}L</small></span>`:'<span class="cell muted">·</span>';
  // rows x cols grid over "a / b" keys; swap when the key is "col / row"
  const matrix=(o,rows,cols,cl,corner,swap)=>{const m=maxAbs(o);return `<table class="mx"><tr><th>${corner}</th>${cols.map(c=>`<th>${esc(cl(c))}</th>`).join("")}</tr>`+
    rows.map(r=>`<tr><td>${esc(r)}</td>${cols.map(c=>`<td>${heat((o||{})[swap?c+" / "+r:r+" / "+c],m)}</td>`).join("")}</tr>`).join("")+"</table>"};
  const relabel=(o,f)=>Object.fromEntries(Object.entries(o||{}).map(([k,v])=>[f(k),v]));
  const ids=k=>String(k).split(" / ").map(x=>ML[x]||who(x)).join(" / ");
  const sbt=(o,label)=>{const r=Object.entries(o||{});return `<div><h3>${label}</h3>`+(r.length?`<table class="mx"><tr><th></th><th>n</th><th>Win</th><th>Net USD</th><th>PF</th><th>Pips/trade</th></tr>`+
    r.map(([n,g])=>`<tr><td>${esc(n)}</td><td>${g.n}</td><td>${pc(g.win_rate)} <span class="muted">${g.wins}W/${g.losses}L</span></td><td>${money(g.net)}</td><td>${g.profit_factor??"–"}</td><td>${money(g.avg_pips_after_cost)}</td></tr>`).join("")+"</table>":'<p class="muted">none yet</p>')+"</div>"};
  const cnt=o=>Object.entries(o||{}).map(([a,n])=>`<span class="chip">${esc(a==="agent"?"automatic":who(a))} · ${n}</span>`).join("")||'<span class="muted">none yet</span>';
  const shift=x=>`<div class="tl"><span class="num muted">${t(x.at)}</span><span class="badge b-${x.kind==="end"?"hold":"entry"}">${esc(ML[x.model]||x.agent)} · ${esc(x.kind)}</span><span>${esc(x.summary)}</span></div>`;
  const byModel=sb.by_arm_model||{}, armOf=Object.fromEntries(arms.map(a=>[armLabel("H-002-"+a),a]));
  const models=[...new Set([...Object.keys(armOf),...Object.keys(byModel)])];
  const lead=Object.keys(byModel).length>1?Object.entries(byModel).sort((x,y)=>y[1].net-x[1].net)[0][0]:null;
  $("scoreboard").innerHTML=`<h3>Model vs model · both H-002 arms trade the same market and bars</h3><div class="cards">`+
    models.map(m=>{const g=byModel[m];return `<div class="mcard"><h4>${esc(m)}${m===lead?' <span class="badge b-entry">leading</span>':""}</h4>
      <div class="sub">${armOf[m]?"arm "+esc(armOf[m])+" · running now":"earlier model"}</div>
      ${g?`<div class="big">${money(g.net,"USD")}</div>${stats(g,30)}`:'<div class="big muted">–</div><div class="stats">no closed trades yet</div>'}</div>`}).join("")+"</div>"+
    `<div class="cols"><div><h3>By session · net USD</h3><div class="hscroll">${matrix(sb.by_arm_session,SESS,arms,a=>armLabel("H-002-"+a),"Session",true)}</div></div>
      <div><h3>By symbol · net USD</h3><div class="hscroll">${matrix(sb.by_arm_symbol,syms,arms,a=>armLabel("H-002-"+a),"Symbol",true)}</div></div></div>`+
    `<div class="cols"><div><h3>Lessons written</h3><div class="chips">${cnt(sb.lessons_by_author)}</div></div><div><h3>Policy changes</h3><div class="chips">${cnt(sb.policy_changes_by_author)}</div></div></div>`+
    `<details><summary>Attribution by who was on duty at entry (includes H-001B)</summary><div class="cols">`+
      sbt(relabel(sb.by_model,ids),"Model on duty")+sbt(relabel(sb.by_model_strategy,ids),"Model / strategy")+sbt(relabel(sb.by_policy_author,ids),"Policy author")+"</div></details>"+
    (sb.shifts.length?`<h3>Shift log</h3>`+sb.shifts.slice(0,5).map(shift).join("")+
      (sb.shifts.length>5?`<details><summary>${sb.shifts.length-5} older entries</summary>${sb.shifts.slice(5).map(shift).join("")}</details>`:""):"");
  const bp=ls.by_pattern||{}, tried=Object.entries(bp).sort((x,y)=>y[1].net-x[1].net), untried=allPat.filter(p=>!bp[p]), pm=maxAbs(bp);
  const bc=ls.by_confluence||{}, conf=["solo","2 agree","3+ agree"].flatMap(c=>[c,c+" · opposed"]).concat(["unknown"]).filter(c=>bc[c]);
  const nb=v=>{const w=Math.min(50,Math.abs(v)/pm*50);return `<span class="netbar"><i style="${v>=0?"left":"right"}:50%;width:${w}%;background:var(${v>=0?"--up":"--down"})"></i></span>`};
  $("learning").innerHTML=!ls.closed_trades?'<p class="muted">No closed H-002 trades yet. Every pattern gets explored first.</p>':
    `<p class="muted">${ls.closed_trades} closed exploration trade${ls.closed_trades==1?"":"s"} · ${tried.length}/${allPat.length} patterns tried · assumed cost ${ls.assumed_cost_pips} pip per trade · under 10 trades is noise</p>`+
    `<div class="cards">`+syms.map(sy=>{const g=(ls.by_symbol||{})[sy];return `<div class="mcard mini"><div class="sub">${esc(sy)}</div>${g?`<div class="big">${money(g.net)}</div>${stats(g,10)}`:'<div class="big muted">–</div><div class="stats">no closed trades</div>'}</div>`}).join("")+"</div>"+
    `<h3>Pattern leaderboard</h3><div class="hscroll"><table class="mx"><tr><th>Pattern</th><th>Trades</th><th>Win</th><th style="text-align:left">Net USD</th><th>Pips/trade</th></tr>`+
      tried.map(([p,g])=>`<tr><td>${esc(p)}</td><td>${g.n}${g.n<10?' <span class="muted">few</span>':""}</td><td>${pc(g.win_rate)} <span class="muted">${g.wins}W/${g.losses}L</span></td><td style="text-align:left">${nb(g.net)}${money(g.net)}</td><td>${money(g.avg_pips_after_cost)}</td></tr>`).join("")+"</table></div>"+
    (untried.length?`<h3>Not tried yet (${untried.length})</h3><div class="chips">${untried.map(p=>`<span class="chip">${esc(p)}</span>`).join("")}</div>`:"")+
    `<h3>Signal agreement · other patterns on the same bar and symbol</h3><div class="cards">`+conf.map(c=>{const g=bc[c];
      return `<div class="mcard mini"><div class="sub">${esc(c)}</div><div class="big">${money(g.net)}</div>${stats(g,10)}</div>`}).join("")+"</div>"+
    `<div class="hscroll">${matrix(ls.by_pattern_confluence,tried.map(([p])=>p),conf,x=>x,"Pattern",false)}</div>`+
    `<div class="cols"><div><h3>Pattern × symbol · net USD</h3><div class="hscroll">${matrix(ls.by_symbol_pattern,tried.map(([p])=>p),syms,x=>x,"Pattern",true)}</div></div>
      <div><h3>Pattern × session · net USD</h3><div class="hscroll">${matrix(ls.by_pattern_session,tried.map(([p])=>p),SESS,x=>x,"Pattern",false)}</div></div></div>`+
    `<h3>Hour of entry (UTC) · trades, tinted by net</h3><div class="hours">${Array.from({length:24},(_,h)=>{const g=(ls.by_hour||{})[h];
      return `<div title="${h}:00 UTC${g?` · ${g.n} trades · net ${g.net}`:" · no trades"}" style="${g?tint(g.net,maxAbs(ls.by_hour)):""}"><b>${h}</b>${g?g.n:"·"}</div>`}).join("")}</div>`;
  const ch=s.policy_changes||[];
  const PF={lots:["Lots",p=>`${p.lots}${Object.entries(p.lots_by_symbol||{}).map(([k,v])=>` · ${k} ${v}`).join("")}`],
    stop:["Stop loss",p=>`max(${p.sl_atr}×ATR, ${p.min_sl_pips} pips)`], target:["Take profit",p=>`${p.tp_rr}R`],
    hold:["Time exit",p=>`${p.max_hold_bars} bars (${p.max_hold_bars*15/60}h)`], explore:["Exploration",p=>`ε ${p.epsilon} · min ${p.min_samples} samples`],
    avoid:["Skip hours",p=>(p.avoid_hours_utc||[]).length?p.avoid_hours_utc.map(h=>h+":00").join(", ")+" UTC":"none"],
    perbar:["Entries per bar",p=>String(p.entries_per_bar??"all matches")], sizing:["Sizing",p=>p.risk_per_trade_pct==null?"fixed lots":p.risk_per_trade_pct+"% risk"],
    cost:["Assumed cost",p=>`${p.assumed_cost_pips} pip`]};
  $("policy").innerHTML=`<div class="cards">`+arms.map(a=>{const p=P[a], last=ch.find(c=>c.arm===a), others=arms.filter(b=>b!==a).map(b=>P[b]), on=p.enabled||[];
    return `<div class="mcard"><h4>${esc(armLabel("H-002-"+a))}</h4><div class="sub">arm ${esc(a)} · v${p.version}${last?` · last change ${t(last.at)} by ${esc(last.author==="system"?"system":who(last.author))}`:""}</div>
      <div class="params">${Object.values(PF).map(([n,f])=>`<div class="${others.some(o=>f(o)!==f(p))?"diff":""}"><span>${n}</span>${esc(f(p))}</div>`).join("")}</div>
      <div class="sub">Symbols</div><div class="chips">${(p.symbols||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join("")}</div>
      <div class="sub" style="margin-top:10px">Patterns enabled ${on.length}/${allPat.length}</div><div class="chips">${allPat.map(x=>`<span class="chip${on.includes(x)?"":" off"}">${esc(x)}</span>`).join("")}</div>
      <details><summary>Raw policy JSON</summary><pre class="mono" style="white-space:pre-wrap;font-size:12px">${esc(JSON.stringify(p,null,1))}</pre></details></div>`}).join("")+"</div>"+
    (ch.length?`<h3>Change history</h3><div class="scroll"><table class="mx"><tr><th>When</th><th>Model (arm)</th><th>Version</th><th>Author</th><th style="text-align:left">Reason</th></tr>`+
      ch.map(c=>`<tr><td class="num">${t(c.at)}</td><td>${esc(armLabel("H-002-"+c.arm))}</td><td>v${c.version}</td><td>${esc(c.author==="system"?"system":who(c.author))}</td><td style="text-align:left;white-space:normal">${esc(c.reason)}</td></tr>`).join("")+"</table></div>":"");
  const VB={supported:"b-entry",contradicted:"b-exit",unclear:"b-hold",observing:"b-hold","no data":""};
  const fmtR=g=>g.avg_r==null?"–":(g.avg_r>0?"+":"")+g.avg_r.toFixed(2)+"R";
  const fmtS=g=>g?`${g.n} events (${g.trades} trades) · win ${pc(g.win_rate)} · ${fmtR(g)} · ${money(g.net)}`:"no trades";
  const fmtArm=o=>Object.entries(o||{}).map(([a,g])=>`${esc(armLabel("H-002-"+a))}: ${g.n} trades, win ${pc(g.win_rate)}, ${fmtR(g)}, ${money(g.net)}`).join("<br>");
  $("playbook").innerHTML=(s.playbook||[]).length?`<div class="cards">`+s.playbook.map(r=>{const e=r.evidence, n=e.matched?e.matched.n:0, w=Math.min(100,n/r.confirm_n*100);
    return `<div class="mcard"><h4>#${r.id} ${esc(r.title)}</h4>
      <div class="sub"><span class="badge ${r.action==="avoid"?"b-exit":r.action==="prefer"?"b-entry":"b-hold"}">${esc(r.action)}</span> <span class="badge">${esc(r.status)}</span>
        <span class="badge ${VB[e.verdict]||""}">${esc(e.verdict)}</span>${e.ready_to_propose?' <span class="badge b-entry">ready to propose</span>':""}</div>
      <div class="chips" style="margin:6px 0">${Object.entries(r.filter).map(([k,v])=>`<span class="chip">${esc(k)} = ${esc(v.join(", "))}</span>`).join("")}</div>
      <div class="wl"><i style="flex:${w}"></i><s style="flex:${100-w}"></s></div>
      <div class="stats"><span>unique events <b>${esc(e.progress)}</b> [${esc(e.label)}]</span></div>
      <p style="margin:8px 0 4px;font-size:13px"><b>Matched:</b> ${fmtS(e.matched)}<br><span class="muted"><b>Others:</b> ${fmtS(e.others)}</span>${e.by_arm?`<br><span class="muted" style="font-size:12px">${fmtArm(e.by_arm)}</span>`:""}</p>
      <p class="muted" style="margin:4px 0 0;font-size:12px">${esc(r.rationale)} · ${esc(who(r.author))}${r.proposal_id?` · proposal #${r.proposal_id}`:""}${r.note?` · ${esc(r.note)}`:""}</p></div>`}).join("")+"</div>"
    :'<p class="muted">No rules yet. Each shift, Claude and Codex distill candidate rules from Hindsight lessons with <code>fxra demo-playbook --add</code>.</p>';
  const fr=v=>v==null?"–":`<span class="${v>0?"ok":v<0?"bad":""}">${v>0?"+":""}${v.toFixed(2)}R</span>`;
  const H=s.h003||{}, HS=s.h003_scoreboard||{by_arm:{},decisions:{},agreement:{n:0,agree:0}}, harms=Object.keys(H);
  const hlab=a=>armLabel("H-003-"+a), sbadge=st=>`<span class="badge ${st==="sent"||st==="approved"?"b-entry":st==="skipped"?"b-hold":"b-exit"}">${esc(st)}</span>`;
  const hleader=harms.filter(a=>HS.by_arm[a]).sort((x,y)=>HS.by_arm[y].net-HS.by_arm[x].net)[0];
  const ag=HS.agreement;
  $("h003").innerHTML=!harms.length?'<p class="muted">H-003 is not running.</p>':
    `<p class="muted">Each closed M15 bar the loop proposes one candidate per symbol, bar and side to every arm. Each model takes or skips it with a reason; only taken ones trade, through the same risk gate.
      Shadow R = what the default stop/target would have made from the bar close after cost: a model's filter helps only if its taken candidates beat its passed ones.</p><div class="cards">`+
    harms.map(a=>{const g=HS.by_arm[a], d=HS.decisions[a]||{}, taken=(d.approved||0)+(d.sent||0)+(d.rejected||0), idle=d.candidates&&!taken&&!d.skipped;
      return `<div class="mcard"><h4>${esc(hlab(a))}${a===hleader&&harms.length>1&&Object.keys(HS.by_arm).length>1?' <span class="badge b-entry">leading</span>':""}</h4>
        <div class="sub">arm H-003-${esc(a)} · ${idle?'<span class="bad">not deciding yet: candidates expire unanswered</span>':`avg decision ${d.avg_latency_min??"–"} min after the bar`}</div>
        ${g?`<div class="big">${money(g.net,"USD")}</div>${stats(g,30)}<div class="stats"><span>avg <b>${fr(g.avg_r)}</b> per trade</span></div>`:'<div class="big muted">–</div><div class="stats">no closed trades yet</div>'}
        <div class="stats" style="margin-top:8px"><span><b>${d.candidates||0}</b> candidates</span><span><b>${taken}</b> taken</span><span><b>${d.skipped||0}</b> skipped</span><span><b>${d.expired||0}</b> expired</span><span><b>${d.pending||0}</b> waiting</span></div>
        <div class="stats"><span>taken shadow <b>${fr(d.shadow_taken)}</b> (${d.n_taken||0})</span><span>passed shadow <b>${fr(d.shadow_passed)}</b> (${d.n_passed||0})</span></div></div>`}).join("")+"</div>"+
    (ag.n?`<p class="muted">Same candidate decided by both models: ${ag.n} · they agreed ${ag.agree} (${pc(ag.agree/ag.n)})</p>`:"")+
    (Object.keys(HS.by_arm).length?`<div class="cols"><div><h3>By session · net USD</h3><div class="hscroll">${matrix(HS.by_arm_session,SESS,harms,hlab,"Session",true)}</div></div>
      <div><h3>By symbol · net USD</h3><div class="hscroll">${matrix(HS.by_arm_symbol,syms,harms,hlab,"Symbol",true)}</div></div></div>`:"")+
    harms.map(a=>{const b=H[a];return `<h3>${esc(hlab(a))} · waiting for a decision (${b.pending.length})</h3>`+
      (b.pending.length?`<div class="hscroll"><table class="mx"><tr><th>Candidate</th><th>Side</th><th style="text-align:left">Patterns for / against</th><th>Agreement</th><th>Expires</th></tr>`+
        b.pending.map(c=>`<tr><td>${esc(c.id)}</td><td><span class="badge b-${c.side}">${esc(c.side)}</span></td><td style="text-align:left">${esc(c.for.join(", "))}${c.against.length?` <span class="muted">vs ${esc(c.against.join(", "))}</span>`:""}</td><td>${esc(c.confluence||"")}</td><td>${c.expires_in_min} min</td></tr>`).join("")+"</table></div>":'<p class="muted">Nothing waiting.</p>')+
      (b.recent.length?`<details${a===harms[0]?" open":""}><summary>Recent decisions and reasons</summary><div class="hscroll"><table class="mx"><tr><th>Candidate</th><th>Decision</th><th>Shadow</th><th style="text-align:left">Reason</th></tr>`+
        b.recent.map(c=>`<tr><td>${esc(c.id)}</td><td>${sbadge(c.status)}</td><td>${fr(c.shadow_R)}</td><td style="text-align:left;white-space:normal">${esc(c.reason||"")}</td></tr>`).join("")+"</table></div></details>":"")}).join("");
  $("proposals").innerHTML=s.proposals.length?`<table><tr><th>#</th><th>By</th><th>Proposal</th><th>Evidence</th><th>Status</th><th>Review</th></tr>`+
    s.proposals.map(p=>`<tr><td>${p.id}</td><td>${esc(who(p.author))}</td><td>${esc(p.text)}</td><td>${esc(p.evidence)}</td><td><b>${esc(p.status)}</b></td><td>${esc(p.reviewer?who(p.reviewer)+": "+p.review:"")}</td></tr>`).join("")+"</table>":'<p class="muted">No proposals yet. A rule is distilled only when the other model agrees.</p>';
  $("mode").textContent="mode: "+(s.mode||"no risk.toml");
  $("kill").innerHTML=s.kill_switch?'<span class="bad">kill switch ON</span>':'<span class="ok">kill switch off</span>';
  $("conn").innerHTML=s.live_error?`<span class="bad">MT5: ${esc(s.live_error)}</span>`:(L.account?'<span class="ok">MT5 demo connected</span>':"MT5: waiting");
  $("updated").textContent="updated "+new Date().toLocaleTimeString();
  const sm=s.summary;
  $("kpis").innerHTML=kpi("Balance",a.balance!=null?(+a.balance).toFixed(2)+" "+ccy:"–")+kpi("Equity",a.equity!=null?(+a.equity).toFixed(2)+" "+ccy:"–")+
    Object.entries(L.markets||{EURUSD:{quote:q}}).map(([sym,m])=>kpi(sym,m.quote?`${m.quote.bid} / ${m.quote.ask}`:"no fresh tick")).join("")+
    (()=>{const g=Object.values((s.h003_scoreboard||{}).by_arm||{}), n=g.reduce((x,v)=>x+v.n,0), w=g.reduce((x,v)=>x+v.wins,0);
      return kpi("H-003 closed trades",n)+kpi("H-003 win rate",n?(w/n*100).toFixed(0)+"%":"–")+kpi("H-003 net",money(g.reduce((x,v)=>x+v.net,0),ccy))})()+
    kpi(`All strategies net (${sm.closed_trades} trades)`,money(sm.net_total,ccy))+
    kpi("Account mode",a.hedging==null?"–":(a.hedging?"hedging":"netting"));
  const PIPS={EURUSD:0.0001,GBPUSD:0.0001,AUDUSD:0.0001,NZDUSD:0.0001,EURGBP:0.0001,USDJPY:0.01,EURJPY:0.01,GBPJPY:0.01,AUDJPY:0.01,XAUUSD:0.1};
  const ps=L.positions||[], nowMs=Date.parse(s.server_time), pipOf=p=>PIPS[p.symbol]||0.0001;
  const patternOf=Object.fromEntries(s.trades.map(r=>[r.entry_id,r.pattern]));
  const ago=ms=>{const m=Math.max(0,Math.round(ms/60000));return m<60?`${m}m`:`${Math.floor(m/60)}h ${m%60}m`};
  const pips=v=>`${v>0?"+":""}${v.toFixed(1)}`;
  const track=p=>{
    if(!p.sl||!p.tp) return "";
    const d=p.side==="buy"?1:-1, lo=p.sl, span=(p.tp-p.sl)*d, PIP=pipOf(p);
    const pos=v=>Math.max(0,Math.min(100,((v-lo)*d)/span*100));
    return `<div class="track" style="--e:${pos(p.price_open)}%"><b style="left:${pos(p.price_open)}%"></b><i style="left:${pos(p.price_current)}%"></i></div>
      <div class="ends"><span>SL ${p.sl} (${pips((p.sl-p.price_open)*d/PIP)})</span><span>TP ${p.tp} (${pips((p.tp-p.price_open)*d/PIP)})</span></div>`};
  const floating=ps.reduce((a,p)=>a+p.profit,0), byArm={};
  ps.forEach(p=>{const k=p.strategy||"?";byArm[k]=(byArm[k]||0)+p.profit});
  $("position").innerHTML=ps.length?
    `<div class="possum">${kpi("Open",ps.length)}${kpi("Floating total",money(floating,ccy))}${Object.entries(byArm).map(([k,v])=>kpi(esc(armLabel(k)),money(v,ccy))).join("")}</div>
    <div class="scroll"><table><tr><th>Model (arm)</th><th>Symbol</th><th>Pattern</th><th>Side</th><th class="right">Entry → now</th><th style="min-width:180px">Stop ↔ target</th><th class="right">Held</th><th class="right">Floating</th></tr>`+
    ps.map(p=>{const d=p.side==="buy"?1:-1, move=(p.price_current-p.price_open)*d/pipOf(p);
      return `<tr><td>${esc(armLabel(p.strategy||""))}</td><td>${esc(p.symbol||"EURUSD")}</td><td>${esc((p.comment||"").startsWith("H3")?"model decision":patternOf[p.comment]||"–")}</td>
      <td><span class="badge b-${p.side}">${p.side} ${p.volume}</span></td>
      <td class="right num">${p.price_open} → ${p.price_current}<br><span class="muted">${pips(move)} pips</span></td>
      <td>${track(p)}</td>
      <td class="right num">${ago(nowMs-Date.parse(p.time))}<br><span class="muted">${t(p.time)}</span></td>
      <td class="right num"><b>${money(p.profit,ccy)}</b></td></tr>`}).join("")+"</table></div>"
    :'<p class="muted">Flat. Each arm opens one position per matched pattern on the next closed M15 bar.</p>';
  const st=s.last_step;
  // decisions run on the first tick after each M15 close (~30 s after :00/:15/:30/:45)
  const M15=900000, b=Math.floor(nowMs/M15)*M15, next=nowMs<b+30000?b+30000:b+M15+30000;
  const hhmm=ms=>new Date(ms).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});
  const h2=(s.orders||[]).filter(o=>o.status==="filled").map(o=>(o.client_order_id||"").match(/^H2C([LX])-(?:[A-Z]{2}-)?(\d{10})/)).filter(Boolean);
  const stamp=h2.map(m=>m[2]).sort().at(-1), inBar=h2.filter(m=>m[2]===stamp);
  const perArm=["L","X"].map(c=>[armLabel("H-002-"+(c==="L"?"claude":"codex")),inBar.filter(m=>m[1]===c).length]);
  const lastBar=stamp?`last entries: bar ${stamp.slice(6,8)}:${stamp.slice(8)} UTC · ${inBar.length} order${inBar.length==1?"":"s"} (${perArm.map(([l,n])=>`${esc(l)} ${n}`).join(", ")})`:"no H-002 orders yet";
  $("step").innerHTML=!st?"–":`<div class="possum">${kpi("Next decision",`${hhmm(next)} <span class="muted" style="font-size:13px">in ${ago(next-nowMs)}</span>`)}${kpi("Last check",ago(nowMs-Date.parse(st.at))+" ago")}</div>
    <p class="muted">${lastBar} · checks every 30 s, decides once per closed M15 bar</p><div class="steps">`+
    Object.entries(st).filter(([k])=>k!=="at").map(([k,r])=>{
      const bar=(r.bar||"").replace(/^H-002[^:]*:(?:[A-Z]{6}:)?/,""), n=(r.entries||[]).length, x=(r.exits||[]).length;
      const what=r.action==="skip"&&r.reason==="already decided for this bar"?"waiting for next bar":r.action;
      const why=n?r.entries.map(e=>`${e.symbol?e.symbol+" ":""}${e.pattern} ${e.side}`).join(", "):x?`${x} time exit(s)`:(r.blocked||[]).length?"risk gate blocked"
        :what==="waiting for next bar"?"already decided this bar":esc(r.reason||"");
      return `<div class="steprow"><b>${esc(armLabel(k))}</b><span class="badge b-${esc(r.action)}">${esc(what)}</span>
        <span class="why">${bar?`<span class="muted">bar ${t(bar)}</span> · `:""}${why}</span></div>`}).join("")+"</div>";
  const cal=s.calendar||{}, CS={ok:'<span class="ok">calendar live</span>',stale:'<span class="bad">calendar stale</span>',
    missing:'<span class="muted">calendar not running</span>',error:'<span class="bad">calendar error</span>'};
  $("calsync").innerHTML=CS[cal.status]||esc(cal.status||"");
  const ev=s.events||[], nowT=Date.parse(s.server_time), num=v=>v==null?"–":(+v).toLocaleString(undefined,{maximumFractionDigits:3});
  $("calendar").innerHTML=cal.status==="missing"?`<p class="muted">The MT5 service <code>FxraCalendarExport</code> is not running yet. Compile <code>mt5_services/FxraCalendarExport.mq5</code> in MetaEditor, then Navigator → Services → Add service → Start. Trading continues without it; news patterns and news tags wait for it.</p>`:
    ev.length?`<div class="hscroll"><table class="mx"><tr><th>Time</th><th>In</th><th>Ccy</th><th style="text-align:left">Event</th><th>Imp.</th><th>Forecast</th><th>Actual</th><th>Previous</th><th>Impact</th></tr>`+
    ev.map(e=>{const d=(Date.parse(e.time)-nowT)/60000, past=d<0;
      return `<tr style="${past?"opacity:.6":""}"><td class="num">${hhmm(Date.parse(e.time))}</td><td class="num">${past?ago(-d*60000)+" ago":"in "+ago(d*60000)}</td><td>${esc(e.currency)}</td>
      <td style="text-align:left;white-space:normal">${esc(e.name)}</td><td>${e.importance>=3?'<span class="badge b-exit">high</span>':'<span class="badge">moderate</span>'}</td>
      <td>${num(e.forecast)}</td><td><b>${num(e.actual)}</b></td><td>${num(e.previous)}</td><td>${e.impact===1?`<span class="ok">good for ${esc(e.currency)}</span>`:e.impact===2?`<span class="bad">bad for ${esc(e.currency)}</span>`:"–"}</td></tr>`}).join("")+"</table></div>"
    :'<p class="muted">No moderate or high importance events in the last 12 h or the next 24 h.</p>';
  $("equity").innerHTML=chart(s.equity);
  const isH3=r=>(r.entry_id||"").startsWith("H3"), trs=ALLT?s.trades:s.trades.filter(isH3);
  const tradeWhat=r=>isH3(r)?hlab((r.entry_id||"").startsWith("H3GX")?"codex":"claude")+" · model":
    r.pattern?"H-002 "+r.pattern+" · "+r.hour_utc+"h":(r.entry_id||"").startsWith("T3-")?"TECH-0003":"H-001B";
  $("trades").innerHTML="<tr><th>Opened</th><th>Who / pattern</th><th>Symbol</th><th>Side</th><th>Lots</th><th>Entry</th><th>Closed</th><th>Exit</th><th>Reason</th><th>Gross</th><th>Comm.</th><th>Swap</th><th>Net</th></tr>"+
    (trs.slice(0,SHOW.trades).map(r=>`<tr><td>${t(r.open_time)}</td><td>${esc(tradeWhat(r))}</td><td>${esc(r.symbol||"")}</td><td>${r.side}</td><td>${r.volume}</td><td>${r.open_price}</td><td>${t(r.close_time)}</td><td>${r.close_price??"open"}</td><td>${esc(r.exit_reason??"")}</td><td>${money(r.profit)}</td><td>${money(r.commission)}</td><td>${money(r.swap)}</td><td>${money(r.net,ccy)}</td></tr>`).join("")||`<tr><td colspan="13" class="muted">${ALLT?"No trades yet.":"No H-003 trades yet. Tick the box to see the H-002 learning data."}</td></tr>`);
  $("decisions").innerHTML="<tr><th>Bar (UTC)</th><th>Strategy</th><th>Action</th><th>Side</th><th>Why</th><th>ATR</th><th>Close</th></tr>"+
    (s.decisions.slice(0,SHOW.decisions).map(d=>{const g=d.details.signal||{},m=d.bar.match(/^(H-002[^:]*):(?:([A-Z]{6}):)?(.*)$/);return `<tr><td>${t(m?m[3]:d.bar)}</td><td>${esc(m?armLabel(m[1]):"H-001B")} · ${esc(m&&m[2]?m[2]:"EURUSD")}${m?" · M15":" · H1"}</td><td><b>${esc(d.action)}</b></td><td>${esc(d.side??"")}</td><td>${esc(d.reason)}</td><td>${g.atr_pips?g.atr_pips.toFixed(1)+" pips":g.atr?(g.atr/0.0001).toFixed(1)+" pips":""}</td><td>${g.close??""}</td></tr>`}).join("")||'<tr><td colspan="7" class="muted">No decisions yet.</td></tr>');
  $("lessons").innerHTML=s.lessons.slice(0,SHOW.lessons).map(l=>`<div class="lesson">${esc(l.text)}<br><small>#${l.id} · ${esc(l.author==="agent"?"agent (automatic)":who(l.author))} · ${t(l.at)}${l.hindsight_document?" · Hindsight: "+esc(l.hindsight_document):" · not yet in Hindsight"}</small></div>`).join("")||'<p class="muted">Lessons appear after each closed trade.</p>';
  more("trades",trs.length,50); more("decisions",s.decisions.length,50); more("lessons",s.lessons.length,10);
}
// long lists show the latest items; the button reveals older ones (kept across refreshes)
const SHOW={trades:50,decisions:50,lessons:10}, BASE={...SHOW};
let ALLT=false;  // trades table: H-003 only unless the learning data is asked for
function more(key,total,step){const shown=Math.min(SHOW[key],total), left=total-shown;
  $(key+"-more").innerHTML=(left>0||SHOW[key]>BASE[key])?`<p class="muted" style="margin:8px 0 0">Showing latest ${shown} of ${total}${total>=200?" (journal view keeps the latest 200)":""}`+
    (left>0?` · <button onclick="SHOW['${key}']+=${step};load()">Show ${Math.min(step,left)} older</button> <button onclick="SHOW['${key}']=1e9;load()">Show all</button>`:"")+
    (SHOW[key]>BASE[key]?` · <button onclick="SHOW['${key}']=BASE['${key}'];load()">Collapse</button>`:"")+"</p>":""}
// Decision log (D-44): structured traces; the written reason is shown as stated, never as internal reasoning
const LOGF={strategy:"H-003",symbol:"",version:"",decision:"",decider:"",since:"",until:""}, LOGOPEN=new Set();
let LOGN=100, LOGD=null, LOGFK="", LBL={};
const ORIGIN={rule:"recorded by the script's rule",model:"stated by the model with its decision",owner:"owner request",derived:"derived afterwards by fxra",outcome:"measured afterwards"};
async function loadLog(){
  const q=new URLSearchParams(Object.entries(LOGF).filter(([,v])=>v)); q.set("limit",LOGN);
  try{ LOGD=await (await fetch("/api/decision-log?"+q)).json() }catch(e){ $("dlog-status").textContent="decision log offline"; return }
  renderLog();
}
function setLog(k,v){LOGF[k]=v;LOGN=100;loadLog()}
function togLog(id){LOGOPEN.has(id)?LOGOPEN.delete(id):LOGOPEN.add(id);renderLog()}
function renderLog(){
  const D=LOGD, F=D.facets, C=D.codes, sy=D.sync||{};
  const fk=JSON.stringify([F,LOGF]);
  if(fk!==LOGFK){LOGFK=fk;  // re-render filters only when they change, so an open dropdown stays open
    const sel=(k,label)=>{const o=[...new Set([...(F[k]||[]),...(LOGF[k]?[LOGF[k]]:[])])];
      return `<label>${label}<select onchange="setLog('${k}',this.value)"><option value="">all</option>${o.map(x=>`<option value="${esc(x)}"${x===LOGF[k]?" selected":""}>${esc(k==="decider"?(LBL[x.split("/")[1]]||x):x)}</option>`).join("")}</select></label>`};
    $("dlog-filters").innerHTML=sel("strategy","Strategy / arm")+sel("symbol","Symbol")+sel("version","Version")+sel("decision","Decision")+sel("decider","Decider")+
      `<label>From (UTC)<input type="date" value="${esc(LOGF.since)}" onchange="setLog('since',this.value)"></label><label>To (UTC)<input type="date" value="${esc(LOGF.until)}" onchange="setLog('until',this.value)"></label>`}
  $("dlog-status").innerHTML=sy.error?`<span class="bad">trace sync failed: ${esc(sy.error)}</span>`:`${D.total} matching · ${sy.open??0} still collecting outcomes`;
  const chip=r=>`<span class="chip o-${r.origin}" title="${esc(((C[r.code]||{}).description||"")+" · "+ORIGIN[r.origin])}">${esc(r.code)}</span>`;
  const fr=v=>v==null?"–":`<span class="${v>0?"ok":v<0?"bad":""}">${v>0?"+":""}${(+v).toFixed(2)}R</span>`;
  const outSum=o=>{if(!o)return '<span class="muted">–</span>';const tr=o.trade,sh=o.shadow,b=[];
    if(tr)b.push(tr.closed?`${money(tr.net)}${tr.R!=null?" · "+fr(tr.R):""}`:'<span class="muted">open</span>');
    if(sh)b.push(`<span class="muted">shadow</span> ${fr(sh.R)}`);
    if(o.verdict)b.push(`<span class="badge ${/win|avoided/.test(o.verdict)?"b-entry":/loss|missed/.test(o.verdict)?"b-exit":""}">${esc(o.verdict.replace("_"," "))}</span>`);
    return b.join(" ")||'<span class="muted">pending</span>'};
  const val=v=>typeof v==="object"?JSON.stringify(v):String(v);
  const kv=o=>{const r=Object.entries(o||{}).filter(([,v])=>v!=null&&v!==""&&!(Array.isArray(v)&&!v.length));
    return r.length?`<table class="kv">${r.map(([k,v])=>`<tr><th>${esc(k)}</th><td class="mono">${esc(val(v))}</td></tr>`).join("")}</table>`:'<p class="muted" style="font-size:12px">none recorded</p>'};
  const rl=rs=>rs.length?`<ul class="rlist">${rs.map(r=>`<li>${chip(r)}${esc((C[r.code]||{}).description||"")}${r.detail?` <span class="muted">· ${esc(r.detail)}</span>`:""}</li>`).join("")}</ul>`:'<p class="muted" style="font-size:12px">none</p>';
  const dname=d=>{d=d||"";const owner=d.startsWith("owner:"), m=owner?d.slice(6):d, l=LBL[m.split("/")[1]]||m;
    return owner?`owner request (via ${l})`:l};
  const detail=x=>{const I=x.inputs||{}, stated=x.reasons.filter(r=>r.origin!=="derived"), derived=x.reasons.filter(r=>r.origin==="derived"), o=x.outcome||{}, P=x.plan;
    const model=(x.decider||"").includes("/");
    return `<div class="dgrid">
      <div class="dbox seen"><h4>1 · Inputs the decider was given</h4><p class="hint">Recorded by the loop at decision time${I.presented_at?`; shown to the model from ${t(I.presented_at)} until ${t(I.expires_at)}`:""}.</p>
        <h5>Market data</h5>${kv(I.market)}${I.position?`<h5>Position</h5>${kv(I.position)}`:""}<h5>Signals and features</h5>${kv(I.features)}<h5>Account</h5>${kv(I.account)}</div>
      <div class="dbox"><h4>2 · Decision as recorded</h4><p class="hint">${model?"What the model submitted with fxra demo-decide.":"The rule outcome the script recorded; scripts write no free-text reasoning."}</p>
        ${kv({decision:x.decision??"pending",status:x.status,version:x.version,policy_version:x.policy_version,decider:dname(x.decider),prompt_ref:x.prompt_ref,confidence:x.confidence,at:x.at})}
        <h5>Coded reasons</h5>${rl(stated)}
        ${x.model_reason?`<h5>Written reason submitted with the decision</h5><blockquote class="stated">${esc(x.model_reason)}</blockquote><p class="hint">Stored exactly as given. It is the model's stated justification, not a record of its internal reasoning.</p>`:""}
        ${P?`<h5>Plan (${esc(P.basis==="shadow"?"not sent: default stop/target from the bar close":"as sent")})</h5>${kv({entry_ref:P.entry_ref,stop:P.stop,target:P.target,lots:P.lots,risk_gate:P.risk&&(P.risk.approved?"approved":"rejected: "+(P.risk.reasons||[]).join(", "))})}
          <h5>Invalidation</h5>${kv(Object.fromEntries(P.invalidation.map(v=>[v.code,v.price??(v.bars+" bars")])))}`:""}</div>
      <div class="dbox post"><h4>3 · Derived afterwards by fxra</h4><p class="hint">Computed from the inputs in 1 when this trace was built, to group and compare decisions. The decider did not state these.</p>${rl(derived)}</div>
      <div class="dbox post"><h4>4 · Outcome, measured after the decision</h4><p class="hint">Broker deals, shadow scoring and M15 bars after the decision. Demo observations, not evidence of edge.</p>
        ${o.trade?`<h5>Trade</h5>${kv(o.trade)}`:""}${o.shadow?`<h5>Shadow (default stop/target from the bar close)</h5>${kv(o.shadow)}`:""}${x.excursion?`<h5>Excursion (approximate, M15 bars)</h5>${kv(x.excursion)}`:""}
        ${(o.checks||[]).length?`<h5>Did the planned conditions happen?</h5>${rl(o.checks.map(c=>({code:c,origin:"outcome"})))}`:""}
        ${!x.outcome?'<p class="muted" style="font-size:12px">Nothing to verify yet, or nothing was planned.</p>':""}</div></div>`};
  const rows=D.traces.map(x=>{const open=LOGOPEN.has(x.id), stated=x.reasons.filter(r=>r.origin!=="derived");
    return `<tr class="lrow${open?" open":""}" data-id="${esc(x.id)}" onclick="togLog(this.dataset.id)"><td class="num">${t(x.at)}</td>
      <td>${esc(x.arm||x.strategy)}<br><span class="muted" style="font-size:12px">${esc(dname(x.decider))}</span></td>
      <td>${esc(x.symbol)}<br><span class="muted" style="font-size:12px">${esc(x.timeframe)} · ${esc(x.session||"")}</span></td>
      <td><span class="badge b-${esc(x.decision||"hold")}">${esc(x.decision||"pending")}</span><br><span class="muted" style="font-size:12px">${esc(x.status)}</span></td>
      <td style="white-space:normal"><div class="chips">${stated.slice(0,4).map(chip).join("")}${stated.length>4?`<span class="muted">+${stated.length-4}</span>`:""}</div></td>
      <td class="num">${outSum(x.outcome)}</td></tr>`+(open?`<tr><td colspan="6" class="ldet">${detail(x)}</td></tr>`:"")}).join("");
  $("dlog").innerHTML="<tr><th>When (UTC)</th><th>Arm · decider</th><th>Symbol</th><th>Decision</th><th>Stated reasons</th><th>Outcome</th></tr>"+
    (rows||'<tr><td colspan="6" class="muted">No traces match these filters.</td></tr>');
  const left=D.total-D.traces.length;
  $("dlog-more").innerHTML=left>0&&LOGN<500?`<p class="muted" style="margin:8px 0 0">Showing ${D.traces.length} of ${D.total} · <button onclick="LOGN=Math.min(500,LOGN+100);loadLog()">Show ${Math.min(100,left)} older</button></p>`:"";
}
loadLog(); setInterval(loadLog,30000);
load(); setInterval(load,10000);
</script></body></html>"""
