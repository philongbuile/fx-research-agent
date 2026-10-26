# Backlog

Ordered by dependency. **Now** items need no broker, pair or timeframe choice. **Blocked** items name the question in `decisions.md` that blocks them.

## Phase 0 — scaffold (this commit)

- [x] B-01 Repo layout, agent rules, docs
- [x] B-02 Hindsight Compose: auth, named volume, bind address, backup/restore runbook
- [x] B-03 Data contracts, immutable raw store with manifests and checksums, `fxra verify`
- [x] B-04 Run record schema and `fxra backtest` (pinned LEAN image, no CLI, no network)
- [x] B-05 Adapter interfaces, CSV bar adapter, dry-run execution, fail-closed risk gate and tests

## Phase 1 — research loop without a broker (Now)

- [x] B-06a Hindsight deployed on this machine (local only), bank `fxra` created, MCP tool allowlist, first backup taken (2026-10-26)
- [x] B-06b Daily backup job: `infra/hindsight/backup.ps1`, Task Scheduler `fxra-hindsight-backup` at 12:30 (2026-10-26)
- [x] B-06d Off-disk copy to `Backups/`, synced by Google Drive (2026-10-26)
- [x] B-06e Restore test: the Drive-synced zip `hindsight-20261026T060702Z.zip` was restored into a throwaway compose project. Row counts and content hashes of `memory_units`, `documents` and `banks` match the live server (2026-10-26)
- [ ] B-06c Connect the second machine over the LAN (D-16). Owner steps: fixed IP, `.env` bind IP, firewall rule (runbook section 2B)
- [x] B-07 CI (GitHub Actions): `uv run pytest`, compose config check, secret scan (gitleaks)
- [x] B-08 Hypothesis template (`experiments/_template/`), plus the Hindsight memory conventions from research-lifecycle.md, as a short agent skill written and reviewed in this repo
- [x] B-09 Run comparison report: table of runs per hypothesis, with trial count and in-sample vs OOS
- [x] B-13 Data quality report per partition: gaps against the expected session calendar, spread stats
- [x] B-14 Export raw store → LEAN data format (`data/lean/`, `fxra export-lean`) with bid/ask quote bars. No time shift: LEAN files use bar open time (D-08 corrected). Golden test against LEAN sample data. Export manifests are recorded in the run record (2026-10-26)
- [x] B-15 Supersede mechanism for corrected data (new batch id, reader picks the latest, old one kept)
- [x] B-16 Walk-forward runner: fixed windows, frozen rules, one OOS pass, all windows recorded
- [x] B-17 Cost stress runner: spread and slippage multipliers as `kind = "stress"` runs

## Phase 1 — blocked on broker / instrument choices

- [ ] B-10 MT5 market data adapter (read-only): `copy_rates_range`, `copy_ticks_range`, `symbol_info` → SymbolSpec + swaps. Windows only. DST and server-timezone tests. *Needs Q-01.*
- [ ] B-11 First real ingestion and data quality review. *Needs Q-01 (Q-02 answered by D-17).*
- [ ] B-12 Research: LEAN ↔ MT5 integration options (community brokerage plugins, a bridge via files or sockets, or our own `IBrokerage`). Review code, license, maintenance and tool permissions before any use. *Needs Q-01.*
- [ ] B-18 Broker vs LEAN/vendor data comparison on overlapping days (R-01). *Needs B-11, B-14.*

## Phase 2 — demo (needs Phase 1 and owner approval)

- [ ] B-19 Widen risk gate modes to `paper` / `demo` through a reviewed change, with per-mode limits
- [ ] B-20 Orders and account snapshots as Parquet datasets; daily reconciliation with the broker
- [ ] B-21 Demo execution adapter (MT5 demo account), idempotent with broker-side reconciliation before any retry; heartbeat and kill switch; no agent access
- [ ] B-22 Demo vs backtest fill comparison report (slippage, spread, swap actually paid)

Live trading is not on this backlog. It needs a separate owner decision after Phase 2.
