# Data contracts

Code is canonical: `src/fxra/contracts.py` (Arrow schemas, dataclasses) and `schemas/run.schema.json`. This page explains them. If the two disagree, the code wins, and you should fix this page.

## Conventions

- **Time is UTC.** Every stored timestamp is timezone-aware UTC with microsecond precision. Adapters convert from the source timezone (for example, MT5 server time) and write the source timezone into the manifest.
- **Bar time is the open time.** LEAN stamps bars with their end time, so the LEAN export step shifts bars by one period.
- **Prices keep their side.** Bars are bid, ask or mid, as declared by `provenance.price_side` in the manifest. Never mix sides in one partition.
- **Points, pips and lots are never implicit.** `SymbolSpec` states `digits`, `point`, `pip_size`, `contract_size` and the volume range and step, per symbol **per source**. Two brokers can differ for the same pair.
- **Spread in points** means `(ask - bid) / point`.

## Raw store layout

```
data/raw/<dataset>/source=<src>/symbol=<SYM>/resolution=<res>/date=YYYY-MM-DD/
    data.parquet      zstd, schema = SCHEMAS[dataset]
    _manifest.json
```

- **Immutable.** Writing an existing partition raises `PartitionExists`. A correction is a new source id (for example `csv-broker-v2`) or a future supersede mechanism (backlog B-15). It is never an overwrite.
- **Atomic.** The write goes to `<partition>.tmp-xxxx`, then a rename. A `<partition>.lock` file is created with `O_EXCL` for the duration of the write.
- **One writer host per source** (D-09). The lock catches accidents. The operating rule prevents them.
- `fxra verify` recomputes every checksum and row count, and reports stale locks and leftover temp directories.

### Manifest (`_manifest.json`)

| Field | Meaning |
|---|---|
| `manifest_version` | 1 |
| `dataset`, `source`, `symbol`, `resolution`, `date` | Partition key |
| `file`, `sha256`, `bytes`, `row_count` | What is in the partition, and its checksum |
| `time_min`, `time_max` | First and last timestamp (UTC) |
| `schema` | Arrow schema string at write time |
| `ingested_at`, `writer.host`, `writer.pid` | When and where it was written |
| `adapter.name`, `adapter.version` | Code that produced it |
| `provenance` | Adapter-specific facts needed to reproduce the fetch: source timezone, price side, file name or query, symbol spec, terminal build |
| `code.git_commit`, `code.git_dirty` | Repository state of the writer |

## Datasets

### `bars`

| Column | Type | Null | Notes |
|---|---|---|---|
| `time` | timestamp(us, UTC) | no | bar open |
| `open`, `high`, `low`, `close` | float64 | no | `low <= min(open, close)`, `high >= max(open, close)` (adapter checks) |
| `tick_volume` | int64 | yes | MT5 tick count, not traded volume |
| `spread_points` | int32 | yes | broker spread at the bar, if the source provides it (MT5 `spread`) |

### `quotes`

`time`, `bid`, `ask` (not null), `bid_size`, `ask_size`. Constraint: `ask >= bid`.

### `swaps`

`observed_at`, `symbol`, `swap_long`, `swap_short`, `swap_mode` (broker-defined unit: points, money or percent), `triple_swap_weekday`. Swaps change over time. They are data and must be observed and stored. Never hard-code them in a config.

### `fills`

`time`, `environment` (`backtest|paper|demo|live`), `run_id`, `client_order_id`, `broker_order_id`, `symbol`, `side`, `quantity_lots`, `price`, `commission`, `swap`, `slippage_points`. Never mix environments in one analysis without filtering on `environment`.

### Order intent, account snapshot (in memory, risk gate input)

`OrderIntent`: `client_order_id` (idempotency key), `strategy_id`, `strategy_version` (git commit), `symbol`, `side`, `quantity_lots`, `order_type`, `stop_loss`, `take_profit`, `limit_price`.
`AccountSnapshot`: `time`, `currency`, `equity`, `day_start_equity`, `open_positions`.
Phase 0 journals these to JSONL (`artifacts/dry-run/journal.jsonl`). The Parquet datasets for orders and account history come in Phase 2 (B-20).

## Run record (`runs/<run_id>/run.json`)

Schema: `schemas/run.schema.json`. Written by `fxra backtest`.

| Field | Meaning |
|---|---|
| `run_id` | `YYYYMMDDTHHMMSSZ-<8 hex>` |
| `kind` | `backtest`, `walk_forward`, `stress`, `paper` or `demo` |
| `hypothesis_id` | Links to `experiments/<id>/` and to Hindsight memories |
| `technical_only` | Smoke tests. Never evidence. |
| `reproducible` | `false` if code was uncommitted. Such a run cannot pass a gate. |
| `code` | `git_commit`, `git_dirty`, `strategy_path` |
| `config` | Path and sha256 of the research config. A copy is stored in the run folder. |
| `engine` | `lean` plus the image tag **and digest** |
| `data` | `source`, `symbols`, `resolution`, `start`, `end`, `timezone`, `manifests` (partition path plus manifest sha256) |
| `assumptions` | `spread`, `slippage`, `swap`, `commission`, `fill_model`. Each one is explicit text. |
| `split` | `in_sample` range, and the `out_of_sample` range or null |
| `metrics` | LEAN `statistics` and `runtime_statistics` from `*-summary.json` |
| `artifacts` | Files in the run folder (LEAN logs, orders, summary) |
| `status` | `created`, `completed` or `failed` |
