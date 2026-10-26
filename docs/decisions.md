# Decisions

"Verified" means checked against the official source on the date shown. "Assumed" means not yet checked; treat it as a risk.

## Settled

| ID | Decision | Basis |
|---|---|---|
| D-01 | QuantConnect LEAN is the engine for strategy code, backtest, paper trading and statistics. | Owner choice. LEAN is Apache-2.0 (verified on GitHub, 2026-10-26). |
| D-02 | MetaTrader 5 is the *expected* data/execution source if the broker uses MT5. No LEAN–MT5 adapter is assumed. | Owner choice. The `MetaTrader5` Python package is published for `win_amd64` only (verified on PyPI 5.0.6231, 2026-10-26). It needs a running MT5 terminal on the same Windows machine. |
| D-03 | Hindsight is memory (hypotheses, decisions, results, postmortems). It is never the source of truth for prices, spreads, swaps, account state, orders or metrics. | Owner choice. |
| D-04 | The Phase 0 structured store is Parquet files plus one JSON manifest per partition, partitioned by source/symbol/resolution/UTC day. No database server. | Parquet and manifests are enough for one writer and a few readers. Revisit when we need concurrent writers or queries across many partitions. |
| D-05 | The Hindsight server starts with `HINDSIGHT_API_LLM_PROVIDER=none`: chunk storage and search only, no fact extraction, and `reflect` returns HTTP 400. | Verified in Hindsight docs (configuration, v1.1). It needs no LLM key, so no third-party data flow by default. See Q-04. |
| D-06 | One Hindsight server, pinned `ghcr.io/vectorize-io/hindsight:1.1.0`, external PostgreSQL (`pgvector/pgvector:0.8.1-pg18`) on a named volume. API key auth through `ApiKeyTenantExtension`; Control Plane UI behind `HINDSIGHT_CP_ACCESS_KEY`. Ports bind to `127.0.0.1` by default and are exposed through `tailscale serve`. | Verified in Hindsight docs v1.1 and the upstream compose file at commit `fa35987` (2026-10-25). |
| D-07 | Run LEAN directly from the pinned image `quantconnect/lean:18206` with `docker run`, not through the LEAN CLI. | QuantConnect docs say: "To use the CLI, you must be a member in an organization on a paid tier" (verified 2026-10-26). The engine image needs no account. Bump the tag only in a reviewed commit. |
| D-08 | All stored times are UTC. A bar's `time` is its **open** time. The adapter records the source timezone in the manifest. | MT5 returns broker server time, which is often UTC+2/+3 and shifts with DST (assumed; verify per broker, risk R-01). LEAN uses bar end time, so the LEAN export must shift bars by one period. |
| D-09 | Each data source has exactly one writer host. Partitions are immutable; the write is temp dir, then rename, with an `O_EXCL` lock file. | Prevents two machines writing the same partition. |
| D-10 | No live orders in Phase 0. The risk gate accepts only `mode = "dry_run"`. The only execution adapter is `DryRunExecution`. | Owner constraint. |
| D-11 | Claude and Codex only propose. A strategy reaches paper, demo or live only as a specific git commit, through the gates in `research-lifecycle.md`, with owner approval. | Owner constraint. |
| D-12 | `AGENTS.md` is the canonical agent rule file. `CLAUDE.md` imports it and adds only Claude-specific notes. | Avoids two rule sets drifting apart. |
| D-13 | Docs and code are in English (open-source audience). | Convention. |
| D-14 | Hindsight is hosted on the owner's Windows dev machine (Docker Desktop), bound to `127.0.0.1`. No Tailscale for now; the second machine connects later. Answers Q-03 for now. | Owner, 2026-10-26. |
| D-15 | The MCP server exposes only an allowlist of tools (`HINDSIGHT_API_MCP_ENABLED_TOOLS` in compose): read, recall, retain, reflect. No delete, clear, update, invalidate or bank management tools for agents. Humans curate through the UI or REST. | Verified 2026-10-26: the single-bank endpoint `/mcp/fxra/` still offers `delete_bank`, `clear_memories` and `delete_document`. With the allowlist, `tools/list` returns 18 tools and none of them delete. |
| D-16 | The second machine reaches Hindsight over the LAN: ports bind to the Docker host's fixed LAN IP, and the host firewall allows 8888/9999 only from the two dev machines. No Tailscale. Answers Q-03. | Owner, 2026-10-26. Plain HTTP on the LAN, so the bearer key is visible to anyone who can sniff that network (R-14). Runbook: `infra/hindsight/README.md` section 2B. |
| D-17 | First instrument for research: **EURUSD**. Store minute bars; the first hypothesis works on **hour** bars (aggregated from minute). | Owner delegated the choice to the agent on 2026-10-26; this is the agent's proposal, confirmed when the owner merges it. Reasons: the most traded pair, so usually the tightest spread and the lowest cost hurdle; LEAN sample data has EURUSD minute bid/ask, so the export golden test (B-14) can use it. Hour bars keep the spread small relative to the bar range. Counterpoint: it is also the most studied pair, so simple published patterns are the most likely to be arbitraged away. This choice says nothing about edge. |

## Open questions

Only Q-01 blocks Phase 1 work. Q-02 is answered by D-17, Q-03 by D-14 and D-16. The rest have defaults that work for now.

| ID | Question | Blocks | Default until answered |
|---|---|---|---|
| Q-01 | Which broker, and does it offer MT5 (demo account first)? Which server? | MT5 data adapter (B-10), symbol specs, real spreads and swaps | CSV import only |
| | *Path agreed for now (2026-10-26): a free MT5 **demo** account (no money) at a broker regulated by a tier-1 regulator (for example FCA, ASIC or CySEC), with a raw-spread account type and published commission. Export bars from the MT5 terminal to CSV and use `fxra import-csv`. That needs no new code. The owner picks the broker.* | | |
| Q-02 | ~~Which pairs and which bar resolution for the first hypothesis?~~ Answered by D-17. | — | — |
| Q-03 | ~~Which machine is the Docker host, and how does the second machine connect?~~ This machine (D-14), over the LAN (D-16). | — | — |
| Q-04 | Should Hindsight use an LLM (for example Anthropic with its own API key) for fact extraction and reflect? That sends memory text to the provider. | Better recall quality only | `none` |
| Q-05 | Account currency, leverage and account type (hedging or netting) of the planned demo account. | Position sizing, risk limits, swap conversion | — |
| Q-06 | Where does the canonical run registry live when two machines run backtests: the Docker host, or each machine plus sync? | Cross-machine run comparison | Local `runs/` plus Hindsight summary |
| Q-07 | Confirm the project license (proposed Apache-2.0, same as LEAN). | Public contributions | Apache-2.0 |
| Q-08 | How do read-only copies of the raw store reach the second machine (rsync, Syncthing, shared mount)? | Research on machine B with real data | Manual copy plus `fxra verify` |

## Verified facts used (2026-10-26)

- Hindsight 1.1: env `HINDSIGHT_API_TENANT_EXTENSION=hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension` plus `HINDSIGHT_API_TENANT_API_KEY`. Clients send `Authorization: Bearer <key>`, and the server returns 401 when the key is missing or wrong. MCP lives at `/mcp/{bank_id}/` (single bank) or at `/mcp/` (multi-bank). `hindsight-admin backup|restore` exists; restore deletes the target schema. API port 8888, UI port 9999.
- LEAN: the engine image `quantconnect/lean` is about 14 GB compressed for amd64. LEAN's repo ships forex sample data (`Data/forex/oanda`, including EURUSD minute data).
- MT5: `pip install MetaTrader5`. Functions include `initialize`, `login`, `copy_rates_range`, `copy_ticks_range`, `symbol_info`, `account_info`, `positions_get`, `orders_get`, `history_deals_get`, `order_check` and `order_send`. The docs do not state the timezone of returned times (risk R-01).
