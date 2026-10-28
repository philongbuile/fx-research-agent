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
| D-08 | All stored times are UTC. A bar's `time` is its **open** time. The adapter records the source timezone in the manifest. | MT5 returns broker server time, which is often UTC+2/+3 and shifts with DST (assumed; verify per broker, risk R-01). LEAN data files also stamp a bar with its **open** time (in the market's data timezone, UTC for `oanda`); LEAN itself sets `EndTime = Time + period` and delivers the bar at `EndTime`. So the LEAN export writes our bars **unshifted**. Corrected 2026-10-26 (B-14): this row used to say the export must shift by one period, which would have delivered every bar one period late. Verified against `quantconnect/lean:18206` sample files and a probe run (see `src/fxra/lean_export.py`). |
| D-09 | Each data source has exactly one writer host. Partitions are immutable; the write is temp dir, then rename, with an `O_EXCL` lock file. | Prevents two machines writing the same partition. |
| D-10 | No live orders in Phase 0. The risk gate accepts only `mode = "dry_run"`. The only execution adapter is `DryRunExecution`. | Owner constraint. Superseded for demo accounts by D-30 (2026-10-28); live remains forbidden. |
| D-11 | Claude and Codex only propose. A strategy reaches paper, demo or live only as a specific git commit, through the gates in `research-lifecycle.md`, with owner approval. | Owner constraint. |
| D-12 | `AGENTS.md` is the canonical agent rule file. `CLAUDE.md` imports it and adds only Claude-specific notes. | Avoids two rule sets drifting apart. |
| D-13 | Docs and code are in English (open-source audience). | Convention. |
| D-14 | Hindsight is hosted on the owner's Windows dev machine (Docker Desktop), bound to `127.0.0.1`. No Tailscale for now; the second machine connects later. Answers Q-03 for now. | Owner, 2026-10-26. |
| D-15 | The MCP server exposes only an allowlist of tools (`HINDSIGHT_API_MCP_ENABLED_TOOLS` in compose): read, recall, retain, reflect. No delete, clear, update, invalidate or bank management tools for agents. Humans curate through the UI or REST. | Verified 2026-10-26: the single-bank endpoint `/mcp/fxra/` still offers `delete_bank`, `clear_memories` and `delete_document`. With the allowlist, `tools/list` returns 18 tools and none of them delete. |
| D-16 | The second machine reaches Hindsight over the LAN: ports bind to the Docker host's fixed LAN IP, and the host firewall allows 8888/9999 only from the two dev machines. No Tailscale. Answers Q-03. | Owner, 2026-10-26. Plain HTTP on the LAN, so the bearer key is visible to anyone who can sniff that network (R-14). Runbook: `infra/hindsight/README.md` section 2B. |
| D-17 | First instrument for research: **EURUSD**. Store minute bars; the first hypothesis works on **hour** bars (aggregated from minute). | Owner delegated the choice to the agent on 2026-10-26; this is the agent's proposal, confirmed when the owner merges it. Reasons: the most traded pair, so usually the tightest spread and the lowest cost hurdle; LEAN sample data has EURUSD minute bid/ask, so the export golden test (B-14) can use it. Hour bars keep the spread small relative to the bar range. Counterpoint: it is also the most studied pair, so simple published patterns are the most likely to be arbitraged away. This choice says nothing about edge. |
| D-18 | The runner passes backtest dates and stress values to the algorithm as LEAN parameters, through a JSON file mounted into the container and given to the launcher as `--config`. The algorithm reads them with `self.get_parameter(...)`. | Verified 2026-10-26 on `quantconnect/lean:18206`: `--parameters` on the command line is accepted but did not reach `get_parameter`, `--parameters.key` is rejected, and `--config <file>` with a `{"parameters": {...}}` block works (the algorithm log shows the values). |
| D-19 | Demo data source for Phase 1: an MT5 **demo** account at **Pepperstone** (Razor account type: raw spread plus commission). Data reaches the store as MT5 desktop "Export bars" CSV through `fxra import-csv` until B-10. The source id is `mt5-pepperstone-demo`. This decision covers demo data only; it is not a choice of where to trade real money. | The owner delegated the choice to the agent on 2026-10-26, and merging confirms it. Pepperstone meets the criteria from Q-01: FCA and ASIC licensed, MT5 offered, and a raw-spread account type. Open risks: MT5 demo accounts expire (30 or 60 days, Pepperstone pages disagree), and expired demo data cannot be recovered, so export early. A Vietnam resident would probably be onboarded to a non-FCA/ASIC group entity, so check this before any live account. Server timezone and DST behavior are still unverified (R-02). |
| D-20 | The project moves to **another laptop** (the "MT5 laptop", Windows). It runs the MT5 desktop terminal, `fxra`, LEAN and Hindsight. This machine (the D-14 host) is cleaned up after the move, and the setup returns to one machine, so D-16 (LAN access) is not needed for now. Broker for the demo: Pepperstone (D-19) if its site works from there; otherwise the `MetaQuotes-Demo` server, opened from inside the MT5 terminal, recorded as source `mt5-metaquotes-demo` (MetaQuotes data, not a real broker's prices or spreads; R-01). | Owner, 2026-10-26. The owner does not want the MT5 app on this machine, the MT5 web terminal cannot export data, and pepperstone.com appears blocked from Vietnam. Migration steps: `docs/backlog.md` "Move to the MT5 laptop". |
| D-24 | Both machines use the same existing Google Drive backup folder, rather than separate per-computer cloud copies. This machine uses an additive scheduled helper; no deletion or conflicting-file overwrite is propagated. Shared archives do not synchronize live Hindsight databases. | Owner, 2026-10-27. Local folder: `D:\Work\codex\gs_learner\Backups`. Runbook: [shared backup sync](../infra/backup-sync/README.md). |
| D-25 | This machine also runs local Hindsight, alongside the other machine. Its previously empty Docker engine gets a new volume restored from the shared archive, with replacement keys active locally and loopback-only ports. This supersedes D-20's assumption that only the other laptop runs Hindsight. | Owner says both machines need Hindsight, 2026-10-27. Local bootstrap, restored table counts, authentication, real document transport, MCP allowlist, restart persistence and a fresh backup were verified. No change was made to the other host. Routing was then unresolved as Q-13; D-27 now settles its policy, with cutover still pending. |
| D-26 | Adopt ai-memory at the reviewed source candidate `0e5488073d7073caa3181bbaf184102b215dcf66` for curated development notes and Claude/Codex handoffs only, with explicit workspace `personal-development` / project `fx-research-agent`. Authorize MCP-only installation and registration for these clients once A-01 verifies the exact artifact, dependencies/licenses and permissions. Use a Linux/WSL2 service on the current development machine; no automatic capture/hooks, bootstrap, cross-project profile or remote LLM/embedding service. | Owner explicitly delegated the ai-memory decision and requested merging the result on 2026-10-27. This settles Q-09's adoption/scope decision; it does not certify an unverified binary or complete A-01..A-03. No further adoption approval is required within this scope. See [review](third-party/ai-memory.md) and [topology](memory-topology.md). |
| D-27 | Both machines use one authoritative Hindsight bank and one separate authoritative ai-memory backend on the current development machine. Keep services bound to loopback; connect the second machine through an authenticated SSH tunnel with verified host identity. The other Hindsight database is preserved as a standby/archive and must leave the shared write path after cutover. If the primary is unavailable, shared memory writes stop; no local-write fallback or automatic promotion. Google Drive synchronizes backup archives only. | Owner delegated the cross-machine memory decision on 2026-10-27. This settles Q-13 and supersedes D-16's direct plain-HTTP LAN path for memory clients. A single authority avoids conflicting histories. Deployment, divergent-history review and both-machine acceptance remain required; this decision claims no remote access or completed cutover. See [cutover and recovery](memory-topology.md). |

## Confirmed order-tool policy (D-28)

Owner decision, 2026-10-28: permit reviewed order-capable MCP servers, skills and
libraries instead of the previous blanket installation ban. Read-only access is
the default. Future demo trading mutations require a trusted service that binds
fresh, explicit owner confirmation to the exact expiring request, enforces risk
checks and prevents duplicate submissions. See [the confirmation contract and
acceptance requirements](security.md#mt5-order-confirmation-contract-d-28).

This supersedes the blanket order-tool prohibition in the previous AGENTS.md,
security policy and README. It does not approve a specific third-party artifact,
authorize any particular order, promote a strategy or enable live trading.
D-10 remains the implementation baseline: `DryRunExecution` only and
`ALLOWED_MODES = {"dry_run"}`. D-11's owner promotion gates still apply. A separate
reviewed implementation and owner-reviewed gate change are required before demo
execution. No confirmation service, execution adapter or acceptance-test result
is claimed by this documentation change.

## Restricted MT5 market-data profile (D-29)

Owner approval, 2026-10-28, after merging PR #20: adopt the reviewed
`vincentwongso/mt5-trading-mcp` commit
`daa905d36135aa1f00017efccc9b39fba7130c3f` for a first-party, native Windows
market-data-only stdio profile. Use its reviewed lifecycle client, not its
all-tools server; deny trading at the backend and expose no raw account
resources, credential arguments, arbitrary code or execution configuration.
Attach to the existing authorized demo terminal without copying credentials.

Local installation, restricted protocol tests and real market-data reads passed;
see [acceptance](local-mt5-mcp-verification.md). Original epochs remain quarantined
until UTC/DST and calendar verification. This does not implement a trading
`MarketDataAdapter`, enable execution, change D-10's risk modes, promote a
strategy or certify native Codex loading before restart. D-28 still governs any
future execution proposal. Source/artifact and dependency limits are recorded in
the [review](third-party/mt5-mcp.md).

Implementation scope update, 2026-10-28: the owner requested continuing through
backtest. A fixture-tested capture adapter admits only a short contemporaneously
observed UTC interval, marks it technical-only and rejects out-of-window reads.
The original historical captures remain quarantined; no historical DST rule or
source session calendar is certified. A no-order LEAN replay checks exact source
delivery under an explicitly separate technical algorithm calendar. See
[bounded replay evidence](mt5-observed-replay.md). D-17's first strategy hypothesis
and all execution/promotion decisions remain separate.

## Autonomous demo execution (D-30)

Owner decision in the Claude session, 2026-10-28: "remove the block on sending
orders to the MT5 demo; give the agent full authority to send orders on MT5 demo;
revisit when trading a real account." The owner also chose the H-001 rules as the
decision policy, with Claude and Codex both allowed to write lessons.

For **demo accounts only**, this supersedes D-10's dry-run-only baseline and
D-28's per-order human confirmation (steps 2-3 of the contract). What remains:

- Orders go only through the first-party `fxra.mt5_execution` adapter. No raw
  `order_send`, upstream all-tools server or ad-hoc script.
- The adapter refuses any account whose trade mode is not demo, and stops if the
  terminal's account identity changes during a session. Live trading stays out of
  scope and needs a new owner decision.
- Every order passes the automatic risk gate (`ALLOWED_MODES` now includes
  `"demo"`): stop loss, `SymbolSpec` lot size, spread, quote age, daily loss,
  max positions, approved `strategy@version` and the kill switch.
- Idempotency and reconciliation from D-28 steps 5-6 still apply: persist the
  `client_order_id` before sending; after a lost response, reconcile with broker
  state and never resend blindly.
- The owner can stop all execution at any time by creating `artifacts/KILL_SWITCH`.
- As part of this decision the owner authorized Claude to create the initial local
  `config/risk.toml` (`mode = "demo"`, EURUSD, `H-001@<commit>`, 0.01 lot, one
  position, 1% daily loss). Later changes to limits or approvals go back to the owner.

Demo results are forward-test observations, not evidence of edge. H-001's frozen
rules are not tuned from demo outcomes; a rule change is a new hypothesis id.

## Demo exploration phase (D-31)

Owner direction, 2026-10-28: the demo stage is for learning and experimenting as
much as possible. Rigid rules should be distilled only after enough experience.
Owner also set 0.1 lot for the new strategy and a daily KPI of 50 USD.

- **H-002 explorer** (`src/fxra/h002.py`, `H-002@explore`, magic `20261029`)
  trades four M15 entry patterns with ATR stop and take profit. When several
  match, an epsilon-greedy choice prefers patterns with better realised results
  but keeps exploring. Every trade is tagged with pattern, UTC hour, session and
  policy version.
- Claude and Codex may change the H-002 **policy** (`artifacts/demo/h002-policy.json`
  through `fxra demo-policy --set ... --reason ...`) without owner approval.
  Policy covers enabled patterns, stop/target multiples, hold time, avoided hours
  and exploration rate. Every change gets a version, author and reason in the
  journal. New pattern *code* still goes through a branch and PR.
- Account safety stays fixed and owner-controlled in `config/risk.toml`: demo
  only, stop loss on every order, max 0.1 lot, two open positions (one per
  strategy), 1% daily loss and the kill switch.
- The 50 USD/day KPI is shown as progress. It is never an optimisation target:
  agents do not raise size or remove stops to chase it.
- Research honesty is unchanged in substance. Exploration results are
  in-sample observations on a zero-spread demo feed, not evidence of edge.
  Learning statistics subtract an assumed 0.8-pip cost. A rule distilled from
  exploration becomes a new frozen hypothesis id and must be tested on data that
  arrives after it is frozen. H-001B stays frozen as the control.
- Owner addendum, same day: losing trades are acceptable; learning volume
  matters most and every profit or loss is knowledge. H-002 therefore carries
  16 patterns, including contradictory trend and fade setups, and the
  explorer's statistics decide which survive.

## Demo duty rotation and model comparison (D-32)

Owner direction, 2026-10-28: Codex (`gpt-6-sol`) is on duty 08:00-18:00 and
Claude (`claude-opus-5-5`) 18:00-08:00, local time, on one machine. Duty is
flexible: `config/duty.toml` or `fxra demo-shift take`. Memory, docs and skills
must stay continuously current across shifts; see
`.claude/skills/fxra-demo-duty/SKILL.md` (mirrored to `.agents/skills/` by
`fxra sync-skills`). Every trade, policy version, lesson and shift is attributed
to the model on duty. `fxra demo-shift status` and the dashboard report win
rate, net, profit factor and average pips per model, so other models (including
lighter LLMs) can later be tested against the same memory and knowledge.
Model comparisons are confounded by the market hours each shift covers and by
small samples. Read them per strategy and session.

Sizing, same day: the owner chose a **fixed 1 lot** for H-002, about 50 USD lost
at the usual 5-pip stop and about 75 USD at the 1.5R target.
`max_lots_per_order = 1.0` in the local `config/risk.toml`; H-002 policy v2
sets `lots = 1.0`. H-001B stays at 0.01 lot as the frozen control. A
`risk_per_trade_pct` sizing mode exists but is off.

## Two-model collaboration arms (D-33)

Owner direction, 2026-10-28: Codex can work around the clock, Claude only
18:00-08:00, so fixed shifts waste Codex time. The two models should learn
together instead. This supersedes the duty split in D-32; attribution and the
scoreboard remain.

- **Duty:** Codex (`gpt-6-sol`) is primary 24/7 (operations, hourly review).
  Claude (`claude-opus-5-5`) is co-duty 18:00-08:00. The roster stays
  configurable (`config/duty.toml`, `fxra demo-shift take`).
- **Two H-002 arms on the same market:** `H-002-claude` (magic `20261029`,
  continuing the original H-002 and its policy history) and `H-002-codex`
  (magic `20261030`, starting from a copy of the Claude arm's policy). Only an
  arm's owner may change its policy (`fxra demo-policy --arm`). Pattern
  statistics used for exploration are pooled across arms as shared knowledge.
- **Positions:** every matched enabled pattern opens its own position, one per
  pattern per arm, unless the arm sets `entries_per_bar`. The owner removed the
  practical cap: `max_open_positions = 50` and daily loss limit 5% in
  `config/risk.toml`. Stops on every order and 1 lot per order are unchanged.
- **Learning together:** both models read all Hindsight lessons. At each
  hand-over (18:00 and 08:00) the leaving model writes a summary with
  proposals, and the arriving model reviews them. A rule is distilled, or a
  pattern disabled on evidence, only through `fxra demo-propose` plus review by
  the *other* agent. New pattern code still goes through a PR that the other
  agent reviews.

## More symbols for exploration (D-34)

Owner choice, 2026-10-28: to learn faster, H-002 (both arms) also trades
GBPUSD, USDJPY, AUDUSD and XAUUSD alongside EURUSD. Lot sizes: 1 lot, except
gold at 0.1 lot, because 1 lot of gold is 100 USD per 1 USD move.

- Pip sizes are explicit per symbol (`trade_journal.PIP_SIZES`; gold 0.1).
- Statistics are reported by symbol and by symbol and pattern.
- Arm policies carry `symbols` and `lots_by_symbol`.
- The risk gate accepts `max_spread_points_by_symbol` (gold 60 points);
  `allowed_symbols` lists all five.
- H-001B stays EURUSD-only as the frozen control.
- EURUSD keeps its decision keys and client ids. Other symbols add a code
  (`GU`, `UJ`, `AU`, `XA`).

## Model labels and immediate lesson sync (D-35)

Owner request, 2026-10-28: show the model actually running, e.g. "Claude Opus 5.5
Medium" and "Codex GPT-6.1 Sol Medium", not the agent name, because models will
change and more models may join.

- `config/duty.toml` `[agents.<name>]` holds `model`, `effort` and `label`.
  Codex's model id is corrected to `gpt-6.1-sol`; the old `gpt-6-sol` id is
  displayed with the Codex label.
- Each H-002 entry stores `arm_model`, the label of the model owning the arm
  at entry, so results stay attributable after a switch. Pre-D-35 entries were
  backfilled with the labels in force that day. The scoreboard leads with
  model vs model.
- Each closed-trade lesson is retained to Hindsight within one agent tick, when
  the host supplies `HINDSIGHT_API_KEY` (loopback, never logged). Failed
  deliveries stay pending and are retried. Trading decisions already use the
  journal's pattern statistics immediately; Hindsight makes the same lessons
  available to every model.

## Open-position cap raised to 200 (D-36)

Owner decision, 2026-10-28: raise `max_open_positions` in the local
`config/risk.toml` from 50 to 200. After D-34 added four symbols, 50 had
become a binding cap (42 open at the time), which conflicts with the D-33
intent of not limiting exploration. The D-33 5% daily loss limit, the 1-lot
maximum and the kill switch are unchanged.

The H-002 rule "one open position per pattern, per arm, per symbol" stays for
now (Claude's recommendation; the owner may revisit). It is a data-quality rule, not a budget rule: stacking the same pattern on the
same symbol produces correlated samples that inflate n without adding
information. Budget constraints for a smaller future account are to be studied
by replaying the journal under that budget, not by limiting exploration now.

## Signal agreement is recorded for learning (D-37)

Owner request, 2026-10-29: several patterns often enter the same symbol on the
same bar with the same stop and target, so their outcomes are shared. Each
closed H-002 trade is now bucketed by signal agreement (`solo`, `2 agree`,
`3+ agree`, with `· opposed` when a pattern on that bar pointed the other way),
derived from the matches already stored at entry, so older trades are covered.
Stats, lessons and the dashboard report it. Entry behaviour is unchanged; using
agreement in pattern ranking is a later step that needs enough samples per
bucket and goes through a proposal and review.

## Open questions

### Simulation and memory decisions (owner request, 2026-10-27)

- D-21: Separate development memory (ai-memory, shared Claude/Codex scope) from research/character memory (Hindsight). No migration or replacement of Hindsight. Adoption and bounded installation scope are now authorized by D-26; exact-artifact review and acceptance remain required; see `third-party/ai-memory.md`.
- D-22: Life and employment are independent. Dismissal retains identity/personality/personal history and permits evidenced reassessment/rehire. Personal debt strictly exceeding USD 50 permanently ends the character; USD 50 itself does not. Company cash, trading capital, personal cash and personal debt remain separate. Only the trusted controller changes these values.
- D-23: Continuous durable event/knowledge storage and Hindsight narrative delivery precede departure. Successors get status/evidence-qualified shared knowledge, versioned skill references, pending work and attributed personal testimony, with their own personal history. Identity and simulation-time filtering are enforced in code. Final sessions have bounded wall time/output and cannot delay controller recovery indefinitely.
- Implementation choice: a local SQLite controller with transactional event IDs, outbox and departure snapshots; no database server and no changes to market-data storage (D-04). Initial development memory activation is curated MCP-only; automatic hooks await separate privacy review. Operational final-session defaults are configurable, not owner-approved business criteria.

| ID | Question | Blocks | Default until answered |
|---|---|---|---|
| Q-09 | Answered by D-26: adopt ai-memory with bounded MCP-only installation authorization after exact-artifact checks. | A-01 passes and service/user registrations are installed locally; A-03 actual client acceptance remains | [Local evidence](local-memory-mcp.md); do not claim completed S1 or second-machine acceptance |
| Q-10 | Which KPI metric, evidence source, pass threshold, evaluation interval and missed-review handling should employment use? | Business KPI automation | Unset; no automatic dismissal; demo uses explicit synthetic fixtures |
| Q-11 | What learning/reassessment evidence, reviewer and waiting period qualify a dismissed character for rehire? | Business rehire automation | Unset; opt-in controller-attestation mechanism exists, without invented eligibility rules |
| Q-12 | What creates/repays personal debt: living costs, salary, borrowing, interest and schedule? What funds initial balances? | Economic simulation | Unset; opt-in evidenced controller adjustments only; trading loss is never personal debt |
| Q-13 | Answered by D-27: one authoritative memory host on this development machine, accessed locally or through authenticated SSH forwarding. | Actual second-machine cutover and acceptance remain | Preserve both histories; no automatic merge, local-write fallback or promotion |

### Existing research questions

- Q-14 (H-001, opened 2026-10-28): after eligible source history and costs are
  verified, owner review must fix exact IS/OOS dates, minimum evidence and
  quantitative stability/cost stresses before the first strategy trial. PR #23
  supplies a proposed design, not a passed gate. See [implementation status and
  prerequisites](h001-implementation.md). No dates or thresholds are inferred
  from quarantined history.

Nothing blocks Phase 1 work now; Q-01 is answered for demo data by D-19. Q-02 is answered by D-17, Q-03 by D-14 and D-16. The rest have defaults that work for now.

| ID | Question | Blocks | Default until answered |
|---|---|---|---|
| Q-01 | ~~Which broker, and does it offer MT5?~~ Demo data: Pepperstone MT5 demo (D-19). Still open: which broker to use for real money, decided after Phase 2. | Live account only | — |
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
