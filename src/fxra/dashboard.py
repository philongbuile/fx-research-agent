"""Local dashboard + agent loop for the demo agent. Stdlib only, binds 127.0.0.1.

GET /            single page (auto-refresh)
GET /api/state   journal read model + latest live snapshot + kill switch status
"""

import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import demo_agent, risk
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
    def __init__(self, journal: Journal, broker, limits_path: Path, *, send=True, interval=30):
        self.journal, self.broker = journal, CachingBroker(broker)
        self.limits_path, self.send, self.interval = Path(limits_path), send, interval
        self.lock = threading.Lock()
        self.last_step = None

    def limits(self):
        try:
            return risk.RiskLimits.load(self.limits_path)
        except (OSError, KeyError, ValueError):
            return None  # missing config = kill switch active (fail closed)

    def tick(self):
        now = datetime.now(timezone.utc)
        with self.lock:
            try:
                result = demo_agent.step(self.journal, self.broker, self.limits(), now, send=self.send)
            except Exception as e:
                result = {"action": "error", "reason": str(e)}
            self.last_step = {"at": now.isoformat(), **result}
        return self.last_step

    def run_forever(self, stop: threading.Event):
        while not stop.is_set():
            self.tick()
            stop.wait(self.interval)

    def state(self):
        limits = self.limits()
        with self.lock:
            data = self.journal.state()
        data.update(live=self.broker.last, live_error=self.broker.error, last_step=self.last_step,
                    kill_switch=risk.kill_switch_active(limits), mode=limits.mode if limits else None,
                    strategy=f"{demo_agent.STRATEGY_ID}@{demo_agent.STRATEGY_VERSION}",
                    approved=bool(limits and f"{demo_agent.STRATEGY_ID}@{demo_agent.STRATEGY_VERSION}"
                                  in limits.approved_strategies),
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
            if self.path == "/api/state":
                self._send(200, json.dumps(agent.state(), default=str).encode(), "application/json")
            elif self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args):
            pass

    return Handler


def serve(agent: Agent, port: int, *, loop=True):
    stop = threading.Event()
    if loop:
        threading.Thread(target=agent.run_forever, args=(stop,), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(agent))
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
.scroll{overflow-x:auto;max-height:420px;overflow-y:auto}.mono{font-family:ui-monospace,Consolas,monospace}
.lesson{border-left:3px solid var(--accent);padding:6px 10px;margin:0 0 10px}.lesson small{color:var(--mute)}
svg{width:100%;height:160px}.muted{color:var(--mute)}
</style></head><body>
<header><h1>FX Demo Agent</h1><span id="strategy" class="pill"></span><span id="mode" class="pill"></span>
<span id="kill" class="pill"></span><span id="conn" class="pill"></span><span id="updated" class="muted"></span></header>
<main>
<section class="card wide"><h2>Account &amp; market</h2><div class="kpis" id="kpis"></div></section>
<section class="card"><h2>Open position</h2><div id="position"></div></section>
<section class="card"><h2>Last agent step</h2><div id="step" class="mono"></div></section>
<section class="card wide"><h2>Equity</h2><div id="equity"></div></section>
<section class="card wide"><h2>Trades (broker deals)</h2><div class="scroll"><table id="trades"></table></div></section>
<section class="card wide"><h2>Decisions, hour by hour</h2><div class="scroll"><table id="decisions"></table></div></section>
<section class="card wide"><h2>Lessons</h2><div id="lessons"></div></section>
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
  $("strategy").textContent=s.strategy+(s.approved?" · approved":" · NOT approved");
  $("mode").textContent="mode: "+(s.mode||"no risk.toml");
  $("kill").innerHTML=s.kill_switch?'<span class="bad">kill switch ON</span>':'<span class="ok">kill switch off</span>';
  $("conn").innerHTML=s.live_error?`<span class="bad">MT5: ${esc(s.live_error)}</span>`:(L.account?'<span class="ok">MT5 demo connected</span>':"MT5: waiting");
  $("updated").textContent="updated "+new Date().toLocaleTimeString();
  const sm=s.summary;
  $("kpis").innerHTML=kpi("Balance",a.balance!=null?(+a.balance).toFixed(2)+" "+ccy:"–")+kpi("Equity",a.equity!=null?(+a.equity).toFixed(2)+" "+ccy:"–")+
    kpi("EURUSD bid / ask",q?`${q.bid} / ${q.ask}`:"no fresh tick")+kpi("Closed trades",sm.closed_trades)+
    kpi("Win rate",sm.win_rate==null?"–":(sm.win_rate*100).toFixed(0)+"%")+kpi("Net P&L (closed)",money(sm.net_total,ccy))+
    kpi("Account mode",a.hedging==null?"–":(a.hedging?"hedging":"netting"));
  const p=(L.positions||[])[0];
  $("position").innerHTML=p?`<table><tr><th>Side</th><td>${p.side} ${p.volume} lot</td></tr><tr><th>Open</th><td>${p.price_open} at ${t(p.time)}</td></tr>
    <tr><th>Stop loss</th><td>${p.sl}</td></tr><tr><th>Now</th><td>${p.price_current}</td></tr><tr><th>Floating P&L</th><td>${money(p.profit,ccy)}</td></tr></table>`:'<p class="muted">Flat. The agent waits for a close outside the 24-hour channel.</p>';
  $("step").textContent=s.last_step?JSON.stringify(s.last_step,null,1):"–";
  $("equity").innerHTML=chart(s.equity);
  $("trades").innerHTML="<tr><th>Opened</th><th>Side</th><th>Lots</th><th>Entry</th><th>Closed</th><th>Exit</th><th>Reason</th><th>Gross</th><th>Comm.</th><th>Swap</th><th>Net</th></tr>"+
    (s.trades.map(r=>`<tr><td>${t(r.open_time)}</td><td>${r.side}</td><td>${r.volume}</td><td>${r.open_price}</td><td>${t(r.close_time)}</td><td>${r.close_price??"open"}</td><td>${esc(r.exit_reason??"")}</td><td>${money(r.profit)}</td><td>${money(r.commission)}</td><td>${money(r.swap)}</td><td>${money(r.net,ccy)}</td></tr>`).join("")||'<tr><td colspan="11" class="muted">No trades yet.</td></tr>');
  $("decisions").innerHTML="<tr><th>Bar (UTC)</th><th>Action</th><th>Side</th><th>Why</th><th>ATR</th><th>Close</th></tr>"+
    (s.decisions.map(d=>{const g=d.details.signal||{};return `<tr><td>${t(d.bar)}</td><td><b>${esc(d.action)}</b></td><td>${esc(d.side??"")}</td><td>${esc(d.reason)}</td><td>${g.atr?(g.atr/0.0001).toFixed(1)+" pips":""}</td><td>${g.close??""}</td></tr>`}).join("")||'<tr><td colspan="6" class="muted">No decisions yet. The agent decides once per closed H1 bar.</td></tr>');
  $("lessons").innerHTML=s.lessons.map(l=>`<div class="lesson">${esc(l.text)}<br><small>#${l.id} · ${esc(l.author)} · ${t(l.at)}${l.hindsight_document?" · Hindsight: "+esc(l.hindsight_document):" · not yet in Hindsight"}</small></div>`).join("")||'<p class="muted">Lessons appear after each closed trade.</p>';
}
load(); setInterval(load,10000);
</script></body></html>"""
