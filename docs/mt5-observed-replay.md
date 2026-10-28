# MT5 observations through a recorded LEAN backtest

Verified locally on 2026-10-28. This completes a **technical** MT5-to-LEAN replay,
not a trading strategy evaluation, historical timezone certification or promotion.

## Successful run and retained trials

Canonical result: `runs/20261028T105113Z-d462e20a/run.json`.

| Run field | Value |
|---|---|
| Status | `completed` |
| Reproducible | `true` |
| Technical only | `true` |
| Code commit | `db059cef6c5794da689bf2cbd5321c70c5167952` |
| Git dirty | `false` |
| Hypothesis | `TECH-0002-mt5-observed-replay` |
| Replay Bars (runtime statistics) | `5` |
| Replay Verified (runtime statistics) | `true` |
| Total Orders | `0` |

These reported run values come from that exact run record. Its log also contains
`MT5_DATA_REPLAY_VERIFIED`. The algorithm checks each bid OHLC and UTC open/end
time against the prepared source observations and rejects duplicates/missing
deliveries. No performance metric is evidence of edge: no orders or cost model
were evaluated. Do not interpret flat equity as a profitable or safe strategy.

All preceding technical engine trials remain in `runs/`:

- `20261028T104052Z-e392aa1e`: failed initialization because LEAN clamps the end
  date to a closed algorithm calendar day.
- `20261028T104814Z-09503c1c`: reached delivery but the harness compared an
  exchange-local bar label directly with UTC.
- `20261028T104951Z-cdb61afe`: failed the Python aware/naive clock comparison.

The [frozen technical hypothesis](../experiments/TECH-0002-mt5-observed-replay/hypothesis.md)
records the input hashes and corrections. No OOS evaluation or parameter search
was performed; these are transport-harness trials, not candidate strategies.

## What was verified, and what remains quarantined

The official [MT5 API documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py)
describes UTC epochs. Actual quotes on this terminal disagree with an independent
UTC clock. No general historical timezone rule was assumed to resolve that
discrepancy. Two checksum-bound, fresh and advancing quote observations bracket
a short, same-weekday intraday window. They support one unchanged integral-minute
offset over that observed window only. Both capture host clocks agreed with the
independent UTC clock tool near collection.

`Mt5CaptureAdapter` accepts only full contiguous minute bars between those
observations, rejects timestamps outside the bounded interval and preserves
original captures. Its manifest explicitly says `historical_dst_verified=false`
and `technical_only=true`. It does not admit the rest of either capture or
certify previous days, DST transitions, weekends or holiday sessions.

Only this observed slice was written through `rawstore.write_partition`. Raw
checksums passed; its export references the exact immutable manifest. Technical
provenance propagates to `export.json`, and `runs.new_run` rejects a non-technical
run over such an export. The full-day quality report remains partial; a short
contiguous window is not a complete trading day. Nothing was forward-filled.

Local artifacts:

- Raw partition:
  `data/raw/bars/source=mt5-demo-1-observed/symbol=EURUSD/resolution=minute/date=2026-10-28/batch=20261028T104050Z-93ce45fc/`
- Export: `data/lean-mt5-observed-20261028T104013Z/`, including `export.json`,
  `replay-check.json` in `forex/oanda/`, preparation and quality reports.
- UTC observation interval and source offsets are in the raw manifest and replay
  validation file. Capture references/checksums are in the hypothesis and manifest.

The engine consumed a read-only OANDA forex mount from this export instead of
its bundled forex sample directory. Its algorithm and validation files were
read-only, with `--network none`. No data was relabelled as old sample history.

## Three clocks in the harness

1. Source epochs are normalized only within the observed interval; stored/exported
   bar-open timestamps are UTC (D-08).
2. The pinned image's OANDA forex database has UTC `dataTimeZone` and
   `America/New_York` `exchangeTimeZone`. LEAN exposes QuoteBar labels in the
   exchange zone; the harness converts them back to UTC for source comparison.
3. The algorithm uses NodaTime's supported fixed-offset `UTC+13:15` calendar.
   This deliberately places this technical window within a calendar day already
   closed when the run starts, satisfying LEAN's end-date clamp. It is **not** a
   broker/source timezone or DST model. Data timestamps and the engine/system
   clock are unchanged. The algorithm rejects observations outside that closed
   day; comparisons use its UTC clock. Do not reuse this harness calendar for
   strategy/session logic.

No engine image or risk gate was changed. The default LEAN benchmark can request
unavailable auxiliary history, but the acceptance criterion is exact observed
forex delivery, not benchmark returns or calendar completeness.

## Reproduce and continue

Use the pinned runtime/image and keep the referenced local captures/partitions.
The frozen run's commit names the implementation. To prepare a new export from
the same inputs, use `infra/mt5-mcp/prepare_replay.py` with the hypothesis's exact
paths and SHA-256 values and a new output directory. Existing exports and raw
partitions are retained; do not overwrite them or fabricate a missing bar.

```powershell
uv run fxra verify --root data/raw
uv run fxra backtest lean/MT5DataReplay/research.toml --data-dir data/lean-mt5-observed-20261028T104013Z
uv run fxra report --hypothesis TECH-0002-mt5-observed-replay
```

Every replay creates another trial. Changing this dated technical config for a
new observation window requires a new committed configuration and provenance.
Re-exporting does not verify a broader historical interval.

Before a meaningful strategy backtest: establish historical UTC/DST and session
rules, obtain a sufficiently complete range and historical cost assumptions,
verify D-17 hour aggregation, and freeze an owner-selected falsifiable strategy
and data split. Follow [the workflow](mt5-lean-workflow.md) and
[research lifecycle](research-lifecycle.md). This technical replay does not
select that strategy or authorize its promotion/execution.
