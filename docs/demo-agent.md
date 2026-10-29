# Demo agent (H-001B) and dashboard

Owner decision D-30 (2026-10-28) lets the agent trade the MT5 **demo** account on
its own. This page is how to run it, watch it and stop it.

## What it does

`fxra demo-run` starts two things in one process:

- **Agent loop** (every 30 s, idempotent per closed H1 bar). It reads a sanitized
  snapshot through `fxra.mt5_execution`, which runs inside the reviewed MT5 runtime.
  It syncs broker deals into trades, writes a lesson for every newly closed trade,
  then decides once per closed hour:
  - **flat**: enter when the closed bar's close breaks the previous 24 bars'
    high (buy) or low (sell). Stop loss = 2 × ATR(14) from the entry quote, held at
    the broker. Size 0.01 lot.
  - **in a position**: exit at market when the close crosses the opposite 10-bar
    channel, or after 24 hours. Otherwise hold.
  - Fewer than 25 consecutive H1 bars (weekend, broker gap) means no trade.
  - Every entry passes `risk.evaluate`. A rejection is journalled as `blocked`
    with its reasons.
- **Dashboard** on <http://127.0.0.1:8765>. It shows balance, equity, quote, the
  open position with floating P&L, the equity curve, a trades table (broker
  gross, commission, swap and net), the hour-by-hour decision log with reasons,
  and lessons.

H-001B is H-001's frozen 24/14/10 rules applied to the **broker's native H1
bars**. H-001 itself requires hours built from complete M1 data, and MT5 M1 data
regularly skips quiet minutes, so the source is different. That makes it a
separate hypothesis id. Demo results are forward-test observations, **not
evidence of edge**. Do not tune H-001/H-001B from them; a rule change means a new
id and a `STRATEGY_VERSION` bump, which the owner must re-approve in
`config/risk.toml`.

## Run

From the repository root, with project-local uv (see `docs/setup.md`):

```powershell
uv run fxra demo-run            # agent + dashboard
uv run fxra demo-step --no-send # one decision, never sends
uv run fxra demo-lesson         # trades, decisions and lessons not yet in Hindsight
```

Defaults: journal `artifacts/demo/journal.sqlite`, limits `config/risk.toml`,
MT5 runtime `artifacts/mt5-mcp-runtime/Scripts/python.exe`, terminal
`C:\Program Files\MetaTrader 5\terminal64.exe`. The terminal must be running and
logged in to the demo account. The bridge refuses any non-demo account, and it
refuses an account that changes while it is running.

The terminal's **Algo Trading** button must be on. Otherwise every order is
rejected with retcode 10027, "AutoTrading disabled by client". A terminal
auto-update can also log the account out, which surfaces as
`MT5_TERMINAL_UNAVAILABLE`. `uv run fxra demo-test-order` sends one risk-gated
0.01-lot round trip (TECH-0003) to check the whole path. It is approved only
while `TECH-0003@exec-check-v1` is listed in `config/risk.toml`; first passed
2026-10-28.

The MT5 IPC connection does not work from a sandboxed shell. Run the command from
a normal terminal, or with the agent's sandbox disabled for that command.

## Stop

- Create `artifacts/KILL_SWITCH` (any content): every new entry is blocked.
  Existing stops at the broker stay in place.
- Or stop the `fxra demo-run` process. On Windows, Ctrl-C on the launching
  shell can leave `uv`'s Python child running; check that no `fxra demo-run`
  process is left before starting another. A second `demo-run` on the same
  port now fails to bind instead of starting a second trading loop.
- Positions opened by the agent carry magic number `20261028` and a comment
  `H1B-<yymmddhh>-<B|S>`. Close them manually in MT5 if needed.

## H-002 explorer (D-31)

Every closed M15 bar, `h002.step` checks four patterns:

- `breakout20`: close beyond the 20-bar high or low.
- `ema_pullback`: in an EMA20/EMA50 trend, the bar touches EMA20 and closes back in the trend direction.
- `bb_rsi_reversion`: close outside the 2σ Bollinger band with RSI14 below 30 or above 70.
- `inside_bar_break`: close breaks the mother bar of an inside bar.

Twelve more exploration patterns were added on 2026-10-28, because the owner
asked for as many as possible: wins and losses both count as data.

- `breakout10`
- `ema_cross_9_21`
- `macd_cross`
- `momentum_candle` (body > 1.5×ATR)
- `engulfing`
- `pin_bar`
- `rsi_cross_50`
- `rsi_extreme_fade` (RSI < 25 or > 75)
- `three_bar_momentum`
- `bb_squeeze_break`
- `failed_breakout_fade`
- `ema50_stretch_fade` (close more than 2×ATR from EMA50)

Some contradict each other (trend vs fade) on purpose. On 9 days of
quarantined M1 data aggregated to M15, at least one pattern matched on about
513 of 613 evaluated bars. That counts frequency only. A pattern starts trading
only once it is in the policy's `enabled` list.

Each pattern is first tried at least `min_samples` times. After that, an
epsilon-greedy choice prefers the best average pips after an assumed cost, and
explores with probability `epsilon`. Stop = max(`sl_atr` × ATR14, `min_sl_pips`).
Target = `tp_rr` × stop. A time exit fires after `max_hold_bars`. Size is
`lots` (1.0 since policy v2, owner choice).

Duty and the per-model scoreboard follow D-32/D-33 and the `fxra-demo-duty`
skill.

## Symbols (D-34, D-41)

H-002 trades EURUSD, GBPUSD, USDJPY, AUDUSD and XAUUSD (D-34), plus the Asia
and London session pairs EURJPY, GBPJPY, AUDJPY, NZDUSD and EURGBP (D-41);
H-001B trades EURUSD only. One bridge call reads all ten (`markets` in the
snapshot). Client ids carry a 2-letter code (EJ, GJ, AJ, NU, EG for the D-41
pairs); JPY pairs use a 0.01 pip. The calendar export covers NZD too.

- Positions and deals carry `symbol`.
- Policy keys: `symbols` (enabled list) and `lots_by_symbol` (gold 0.1).
- Client ids for non-EURUSD symbols add a code, e.g. `H2CX-UJ-2610281600-07B`.

## Two arms (D-33)

H-002 runs as two arms on the same bars, `H-002-claude` (magic `20261029`) and
`H-002-codex` (magic `20261030`). Each has its own policy file,
`artifacts/demo/h002-policy-<arm>.json`. Only the arm's owner may edit it:

```powershell
uv run fxra demo-policy --arm codex --author codex/gpt-6.1-sol --disable pin_bar --reason "..."
```

Every matched enabled pattern opens its own position, one per pattern per
arm, with client ids `H2CL-<yymmddHHMM>-<pattern#><B|S>` or `H2CX-...`.
Proposals for shared rules go through `uv run fxra demo-propose`, and the
other agent reviews them.

Reflection, by Claude or Codex:

```powershell
uv run fxra demo-policy                      # policy + stats by pattern, hour, pattern x session
uv run fxra demo-policy --set tp_rr=2 --set 'avoid_hours_utc=[21,22]' --reason "<what the stats showed>" --author codex
```

Small samples (n < 10, flagged on the dashboard) are noise. Prefer adding an
observation to Hindsight over changing the policy after a few trades.

Signal agreement (D-37): patterns often fire together on the same bar and
symbol (for example breakout10, breakout20 and three_bar_momentum on one
up-move), so their positions share entry, stop and target and their results
are not independent. Each closed H-002 trade is bucketed by how many patterns
on its bar agreed with its side (`solo`, `2 agree`, `3+ agree`) and whether
any pattern pointed the other way (`· opposed`), from `entry_tags.features.matches`.
`demo-policy` reports `by_confluence` and `by_pattern_confluence`; the
dashboard shows them under "Signal agreement", and lessons state the bucket.
This is recorded for learning only and does not change how entries are taken.

Playbook (D-39): candidate rules distilled from lessons live in the journal's
`playbook` table. Each rule has an action (`avoid`, `prefer`, `watch`), a trade
filter over pattern, symbol, session, hour, side, arm, vol, h1_trend, dow and
confluence, a rationale and a confirmation threshold `confirm_n` (at least 10).
`fxra demo-playbook` recounts each rule's matched-vs-other evidence and labels it
supported, contradicted or unclear. Samples are unique entry events (symbol,
entry bar, side): both arms and every pattern firing the same way on a bar open
copies of one trade, so they count once (Codex review of proposal #1). Per-arm
trade stats are shown beside the event counts. An author can take back an open
proposal with `fxra demo-propose --withdraw <id> --note "..." --author <agent>/<model>`. A supported
rule that reaches `confirm_n` is "ready to propose"; it changes a policy only
after a `demo-propose` agreed by the other model. The dashboard shows the
playbook and the hourly digest retains it to Hindsight as `fxra-playbook`.

```powershell
uv run fxra demo-playbook
uv run fxra demo-playbook --add "Avoid breakouts in the FOMC hour" --action avoid --when pattern=breakout10,breakout20 --when hour=18 --why "FOMC 2026-10-28 lesson" --confirm-n 30 --author claude/claude-opus-5-5
uv run fxra demo-playbook --status 1 proposed --proposal 2 --author claude/claude-opus-5-5
```

## H-003: the model decides each entry (D-42)

H-002 is a script: every matched pattern opens a position. H-003 (`src/fxra/h003.py`,
arm `H-003-claude`, magic `20261031`) puts the model in the loop:

1. Each closed M15 bar the loop writes **candidates** to the journal's
   `candidates` table, one per symbol, bar and side
   (`H3GC-[code-]<yymmddHHMM>-<B|S>`). A candidate lists the patterns for and
   against, signal agreement, per-pattern stats, session, trend, volatility,
   news and open positions on the symbol, and the default stop and target
   (`max(1.5 x ATR, 8 pips)`, 1.5R). Nothing is sent.
2. The model reads them and decides, with a reason (at least 10 characters):

   ```powershell
   uv run fxra demo-decide                     # pending candidates + how past decisions turned out
   uv run fxra demo-decide --take H3GC-XA-2610291430-B --sl-atr 2 --tp-rr 1.5 --reason "..." --author claude/claude-opus-5-5
   uv run fxra demo-decide --skip H3GC-EG-2610291430-S --reason "..." --author claude/claude-opus-5-5
   uv run fxra demo-decide --skip-rest --reason "..." --author claude/claude-opus-5-5
   ```

   Stop 1-3 x ATR (at least 8 pips) and target 1-3R are the model's choice.
   Lots follow the H-002 Claude arm policy and cannot be changed here.
3. Within 30 seconds the loop sends approved candidates through the risk gate
   and kill switch, using the candidate id as the client order id. Positions
   are time-exited after 8 bars (`H3X-<ticket>`). A candidate not answered in
   12 minutes expires; an approval the loop could not send within 5 minutes
   after that expires too.
4. Every candidate gets a **shadow outcome** once 8 later bars have closed:
   the default stop/target from the bar close, stop first when one bar touches
   both, minus the assumed 0.8-pip cost. The dashboard and `demo-decide` compare
   the shadow R of taken and passed candidates. That comparison, on future
   data, is the test of whether the model's filter helps.

The model is invoked by a Claude desktop scheduled task every 15 minutes that
follows `.claude/skills/fxra-demo-decide/SKILL.md`. The app must be open. H-003
trades are tagged `strategy H-003` with no pattern, so they stay out of the
H-002 per-pattern stats; their lessons quote the model's reason. The risk gate
rejects H-003 until the owner adds `H-003@gated-v1` to `approved_strategies`.

## Lessons and memory

- `fxra demo-step` writes a factual lesson row for each closed trade. It leads
  with the setup and conditions (pattern, side, symbol, session, UTC hour,
  weekday, volatility regime, H1/M15 trend) and the result in R, then the planned
  stop/target, ATR, spread, other signals on that bar, broker P&L and
  excursions. H-002 entries store these conditions in `entry_tags.features`
  (`h002.market_context`). Excursions use M15 bars; the snapshot skips the open
  bar, so the realised move is a floor.
- The agent loop retains each lesson to Hindsight with the usual tags plus facet
  tags `outcome:`, `exit:`, `hour:`, `dow:`, `vol:`, `h1_trend:`, `m15_trend:`.
- Single-trade lessons are n=1 and Hindsight cannot count. Once an hour the loop
  retains a digest computed from the journal (`fxra.lesson_insights`): the
  document `fxra-insight-overview` and one `fxra-insight-pattern-<pattern>` per
  pattern, tags `demo`, `insight`. Each line gives n, win rate, average R after
  the assumed cost and a sample label (n<10 noise, n<30 weak). A document is
  re-retained under the same id only when its content changes.
  `uv run fxra demo-insights` prints the digest; `--retain` pushes it now.
- A Claude or Codex reflection session runs `uv run fxra demo-lesson`, reads the
  trades, recalls earlier lessons from Hindsight bank `fxra` (tags `demo`,
  `H-001B`, `lesson`), and writes a narrative lesson there. It records it with
  `uv run fxra demo-lesson --add "<text>" --trade <position_id> --author claude|codex --hindsight-document <id>`
  (or `--mark-retained <lesson_id> <document_id>` for an agent-written row).
- Development progress goes to ai-memory (`personal-development/fx-research-agent`),
  never trade results. Trade results and lessons go to Hindsight, never secrets
  or account identifiers.

## Safety boundaries kept from D-28

- There is no raw `order_send` path. The bridge offers only market entry with a
  stop loss, and whole-position close of the agent's own positions.
- The `client_order_id` is persisted before sending. A known id is never resent.
  A lost response is reconciled against positions and deals.
- Account login and server stay in process memory. Output uses the alias `demo-1`.

## Economic calendar (D-40)

The MT5 terminal has an economic calendar that Python cannot read directly, so
a small first-party MQL5 service exports it:

1. Copy `mt5_services/FxraCalendarExport.mq5` into the terminal's
   `MQL5\Services` folder (File → Open Data Folder) and compile it in
   MetaEditor, or from a shell with `metaeditor64.exe /compile:<path>`.
2. In the terminal's Navigator, right-click Services → Add service →
   FxraCalendarExport → OK. It starts at once and every time the terminal starts.
3. Every 5 minutes it writes `fxra_calendar.json` (30 days back, 7 days ahead)
   to `%APPDATA%\MetaQuotes\Terminal\Common\Files`. It only reads the calendar;
   it never trades.

The agent loop reads the file each tick, stores events in the journal table
`econ_events`, tags every H-002 entry with the nearest relevant event
(`event`: `pre|release|post` + `high|moderate`, plus name, currency, minutes
from release and MT5's good/bad impact) and evaluates three news patterns on the
first closed M15 bars after a high-importance release for the symbol's
currencies:

- `news_momentum`: the release bar's range is at least one ATR → follow it;
- `news_surprise`: MT5 marks the value good or bad for its currency → trade
  that currency's direction;
- `news_followthrough`: the bar after the release continues the release bar's
  direction → enter once the first spike is confirmed.

`uv run fxra demo-calendar` lists the last 6 hours (actual vs forecast) and
the next 24 hours. The dashboard shows the same table and a calendar status
pill. Trades from before the export are tagged from the stored history.