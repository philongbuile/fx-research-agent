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
- Or stop the `fxra demo-run` process.
- Positions opened by the agent carry magic number `20261028` and a comment
  `H1B-<yymmddhh>-<B|S>`. Close them manually in MT5 if needed.

## H-002 explorer (D-31)

Every closed M15 bar, `h002.step` checks four patterns:

- `breakout20`: close beyond the 20-bar high or low.
- `ema_pullback`: in an EMA20/EMA50 trend, the bar touches EMA20 and closes back in the trend direction.
- `bb_rsi_reversion`: close outside the 2σ Bollinger band with RSI14 below 30 or above 70.
- `inside_bar_break`: close breaks the mother bar of an inside bar.

Each pattern is first tried at least `min_samples` times. After that, an
epsilon-greedy choice prefers the best average pips after an assumed cost, and
explores with probability `epsilon`. Stop = max(`sl_atr` × ATR14, `min_sl_pips`).
Target = `tp_rr` × stop. A time exit fires after `max_hold_bars`. Size is
`lots` (1.0 since policy v2, owner choice).

Duty rotation and the per-model scoreboard follow D-32 and the
`fxra-demo-duty` skill.

Reflection, by Claude or Codex:

```powershell
uv run fxra demo-policy                      # policy + stats by pattern, hour, pattern x session
uv run fxra demo-policy --set tp_rr=2 --set 'avoid_hours_utc=[21,22]' --reason "<what the stats showed>" --author codex
```

Small samples (n < 10, flagged on the dashboard) are noise. Prefer adding an
observation to Hindsight over changing the policy after a few trades.

## Lessons and memory

- `fxra demo-step` writes a factual lesson row for each closed trade: result,
  costs, exit reason and approximate excursions.
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
