# Local MT5 market-data MCP verification

Verified on the current Windows development machine, 2026-10-28. The owner
approved PR #20's exact review and requested continuing installation. PRs #18,
#19 and #20 are merged; the checkout was fast-forwarded to main before this work.

## Installed scope

The [reviewed candidate](third-party/mt5-mcp.md) is installed in the ignored
`artifacts/mt5-mcp-runtime` environment, separate from the research `.venv`.
Native Python 3.13.9 was reused. The candidate wheel and upstream lock pins were
verified; no third-party build or install script was executed.

The first-party entry point `fxra.mt5_readonly` uses upstream `MT5Client` with a
deny-by-default native facade. It registers exactly:

- `mt5_data_status`
- `mt5_symbol_spec`
- `mt5_quote`
- `mt5_capture_bars`

No upstream server registry, config watcher, account/position resources, raw
Python, HTTP/RPyC bridge, order-check/send tools or execution flags are exposed.
The connection verifies demo mode, the explicit installation path and identity
continuity within the service lifetime. Credentials remain in MT5; only the
alias `demo-1` is returned. The profile is restricted to existing D-17 EURUSD M1
research reads. Restart binds the currently logged-in demo identity afresh; this
is not a persistent account pin or a sandbox against host shell/Python access.

`mt5-data` is registered in Codex user configuration with absolute executable,
module, terminal/capture paths and `PYTHONPATH`. Existing settings were compared
and preserved, with a private pre-edit backup. This active chat has not reloaded
the new server; restart Codex and call the native tools before claiming in-app
acceptance. Claude registration/testing remains deferred.

## Validation and real data

- All 116 existing plus new regression tests pass. New tests cover backend deny rules,
  terminal mismatch, live/account switching, identity changes during a read,
  capture preservation, redaction, count/symbol bounds and crossed quotes.
- `infra/mt5-mcp/probe.py` exercised actual MCP initialization, tool enumeration,
  empty resources and tool calls over stdio with a fake and the real terminal.
  Forged order/check/modify/close/cancel/login/execute names were rejected even
  with a supplied approval flag. Invalid capture bounds were rejected and
  exception messages were sanitized. Successful calls returned real status,
  contract specification, quote and closed bars.
- `uv pip check` passes for the isolated runtime. Installed notices/hashes and
  native protocol evidence are ignored under `artifacts/mt5-mcp-review/`; previous
  protocol files are retained before updating the current evidence file.
- Canonical initial capture for the current profile:
  `data/mt5-quarantine/20261028T092714Z-2b577b48/capture.json`, SHA-256
  `363ba1ccd0e8b878f2c3021eff5b6de82f7e8f538e8b81732b44d827ebadcc06`.
  It preserves raw epochs, symbol fields, quote observation, exact software
  versions, profile hash, request and structural checks. Earlier captures remain.

Captured bars are ordered and structurally valid in this sample, but gaps need
calendar assessment. Quote validity means finite, positive, non-crossed bid/ask,
not verified freshness or tradability. Earlier read probes observed crossed
quotes; current snapshots do not establish that the feed is always sound.

Raw quote epoch labels differ from the independent UTC capture clock. An
instantaneous offset estimate is not a historical timezone/DST specification.
Current swap fields are observations, not a historical cost series. The service
therefore reports `research_ready: false` and does not normalize timestamps,
write raw-store partitions, export to LEAN or launch a strategy backtest.

No order, strategy promotion, risk-mode change, MT5 security-setting change or
live trading occurred. A read success does not establish D-28 execution
acceptance. Follow [the study workflow](mt5-lean-workflow.md) and
[operations](../infra/mt5-mcp/README.md) for subsequent work.
