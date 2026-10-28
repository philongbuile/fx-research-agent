# MT5 demo data-readiness study

Purpose: establish whether a native demo feed can be captured reproducibly for
later LEAN research. This is an infrastructure/data-quality study, not a strategy
hypothesis, backtest result, promotion or evidence of edge.

## Evidence and outcome

The restricted MCP profile retrieved EURUSD minute observations from the
authorized existing demo terminal. The recorded request skips the current bar;
its bounded count does not establish coverage of a complete requested date
range. Original epochs, specification, quote snapshot, profile/version evidence
and structural checks are preserved in the local capture:

`data/mt5-quarantine/20261028T092714Z-2b577b48/capture.json`

SHA-256: `363ba1ccd0e8b878f2c3021eff5b6de82f7e8f538e8b81732b44d827ebadcc06`.
These files are intentionally ignored; inspect them on the capture host. See
[deployment evidence](../docs/local-mt5-mcp-verification.md) for the MCP checks.

The captured bar sequence passed structural checks, including price bounds and
timestamp ordering. It contains intervals without bars that require session and
source analysis. The quote observation retained with this capture is not
crossed; earlier tool probes observed crossed quotes, so a single acceptable
snapshot cannot certify the feed. Preserve and flag anomalous observations.

## Why this capture is not ready for strategy evaluation

- The raw timestamp domain and historical DST convention are unverified. Do not
  apply a current inferred offset to the full history or label raw epochs UTC.
- A missing minute can reflect no quotes, a session closure or incomplete
  history. The default OANDA calendar is not automatically this demo's calendar.
- Minute spread fields do not recover intrabar bid/ask paths. Current swap
  observations do not recover historical swap charges; commission and realistic
  slippage remain unverified.
- A demo feed is not evidence of achievable broker fills. There is no strategy
  rule, frozen split or cost-complete research configuration in this study.

## Next falsification work

Verify timestamp interpretation against fresh source observations and independent
UTC, then verify historical summer/winter/transition behavior. Preserve a source
calendar and inspect gaps without silent filling. Implement a fixture-tested
`MarketDataAdapter` only once those inputs are established, writing through
`rawstore.write_partition`. Verify/export the resulting immutable partitions
before freezing an owner-selected hypothesis under D-17 and the research lifecycle.

No new backtest trial was executed for this study, so there is no new `run_id` or
performance claim. Any future technical or strategy backtest must create and
retain its own run record. The earlier LEAN smoke run remains technical-only.
