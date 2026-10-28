# MT5 to LEAN: supervised research workflow

## Current state

Local LEAN engine verification passed; see [the recorded technical run](local-lean-verification.md).
D-28 permits reviewed integrations, but the [MT5 MCP candidate](third-party/mt5-mcp.md)
has not been installed or accepted. This workflow is a plan, not an executed
study or evidence of edge. MT5 GUI history retrieval worked, but CSV export
stalled; no successful MT5 file pull or structured ingestion is claimed.

## Roles and data path

MT5 provides broker/demo-source observations. The restricted MCP supplies
bounded market-data reads; it does not choose a strategy or send orders. The
data adapter validates and writes immutable raw partitions. LEAN runs a frozen
algorithm over an explicit export without network access. `runs/<run_id>/run.json`
owns provenance, trial accounting and reported metrics. Hindsight stores research
narrative with run references; ai-memory stores development handoffs only.

```text
MT5 terminal -> restricted market-data MCP -> original capture + checksum
    -> verified timezone/symbol contract -> MarketDataAdapter -> raw store
    -> verify + quality -> LEAN export + provenance -> recorded backtest
    -> robustness -> frozen OOS/walk-forward -> reviewed research note
```

## 1. Prove connectivity and preserve the original capture

- Attach to the authorized existing demo terminal; verify demo mode privately,
  expose an alias only. Do not copy credentials or raw account/system metadata.
- Start with a bounded read of EURUSD M1 (existing D-17), excluding the current
  unfinished bar. Record requested range separately from returned first/last
  timestamps, gaps and truncation. Check the MCP result, not only terminal charts.
- Save the original raw epochs, OHLC, tick volume, spread and real volume as
  supplied, capture UTC time, exact source/package versions, symbol metadata and
  file SHA-256. Do not overwrite old captures. A bounded count is not proof of a
  complete date range. Do not import a screenshot or an LLM transcription.
- Preserve each read outcome, including timeout or empty history; never invent
  bars. Use a demo source alias; MetaQuotes sample feed is not Pepperstone data
  and must not be relabelled as a broker's execution conditions (D-19/D-20).

## 2. Verify time and the symbol contract

- Establish whether the returned epochs are UTC or server wall-clock labels.
  Cross-check fresh ticks against an independent UTC clock and terminal display;
  document uncertainty/latency. A current offset alone does not establish DST
  rules or the offset on historical dates. Verify winter/summer and transition
  examples before a range crossing DST is accepted. Never automatically apply
  today's offset to every historical bar.
- Read digits, point, contract size, volume minimum/maximum/step and currencies
  from the actual symbol. Record the pip convention explicitly. A fixture's
  specification is not a broker specification.
- Confirm chart price side and spread units/meaning from the source. MT5 minute
  bars record the minimum bid/ask spread within the minute, not the full intrabar
  spread path ([MetaQuotes price-data documentation](https://www.metatrader5.com/en/terminal/help/trading_advanced/price_data)).
  Reconstructed ask OHLC is approximate; it cannot recover quote-level fills or
  rollover spikes. Capture bid/ask ticks when the hypothesis needs that fidelity.
- Preserve current swap observations with their units and observation time;
  today's swap is not a historical swap series. Do not assume absent commission
  or zero slippage proves zero cost. Unverified inputs block profitability claims.

## 3. Import, verify, inspect quality and export

Implement MT5 ingestion as a `MarketDataAdapter` with fixture tests and writes
through `rawstore.write_partition` only. Until implemented, normalize a verified
capture into the existing CSV contract, preserving a mapping to the original
capture and its checksum. Do not fabricate a timezone just to satisfy TOML.

After the inputs are verified, fill `config/import-csv.example.toml` in an ignored
local config. The following are templates; placeholders are not ready to run:

```powershell
uv run fxra import-csv <verified-import-config> --root data/raw
uv run fxra verify --root data/raw
uv run fxra quality --root data/raw --source <source-alias> --symbol EURUSD --resolution minute --start <date> --end <date>
uv run fxra export-lean --root data/raw --out <separate-export-dir> --source <source-alias> --symbol EURUSD --resolution minute --start <date> --end <date>
```

Inspect missing/duplicate/out-of-order bars, invalid OHLC, spread nulls/outliers,
weekend boundaries, session calendar and export manifest/checksums. The default
quality calendar and LEAN OANDA market calendar may differ from this source;
record the difference rather than interpreting every gap as a vendor defect.
No silent forward fill. Keep timestamps as bar-open times (D-08).

## 4. Create a study before running variants

Use `experiments/_template/` for an owner-selected falsifiable hypothesis; no new
strategy is selected by this workflow. Specify mechanism, entry/exit/stop logic,
missing-data/weekend behavior, lot conversion, realistic costs, failure criteria,
data boundaries and frozen train/test split. D-17 specifies the first hypothesis
on hour bars aggregated from minute data; verify that aggregation and its time
boundaries explicitly before using it. A working minute export does not prove
that an hour aggregation step has been implemented or validated.

Commit reviewed `lean/<Name>/main.py` and `research.toml` before a reproducible
trial. Cite the exact data export and use matching source/range settings. For
an engine-plumbing exercise, keep `technical_only = true`; do not turn the
buy-and-hold smoke fixture into a strategy candidate by renaming it.

## 5. Run and learn without contaminating the holdout

```powershell
uv run fxra backtest <research.toml> --data-dir <verified-export-dir>
uv run fxra report --hypothesis <hypothesis-id>
```

Every backtest variant is a trial, including failures and technical-only runs.
Keep each run and inspect status, code commit/dirty flag, data manifests, cost
assumptions and engine digest in its own `run.json`. In-sample and technical-only
results are not evidence of edge. Freeze perturbations/cost stresses in advance;
touch OOS once under the research lifecycle gates. A changed design after seeing
OOS needs a new hypothesis ID. No parameter tuning on the holdout.

Write a short study note: hypothesis, data limitations, all run IDs, what failed,
what remains untested and the next falsification step. Metrics come from the
cited run records. Hindsight keeps the narrative and run links, not raw captures
or authoritative metrics. Strategy promotion and execution remain separate
owner-reviewed decisions under [the lifecycle](research-lifecycle.md) and D-28.
