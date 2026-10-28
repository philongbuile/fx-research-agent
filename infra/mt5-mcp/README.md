# Restricted local MT5 market-data MCP

Owner-approved candidate: `vincentwongso/mt5-trading-mcp` at
`daa905d36135aa1f00017efccc9b39fba7130c3f`; see
[the artifact review](../../docs/third-party/mt5-mcp.md) and
[local acceptance](../../docs/local-mt5-mcp-verification.md).

The installed upstream wheel supplies the `MT5Client` lifecycle wrapper. The
entry point is first-party `fxra.mt5_readonly`, **not** upstream `mt5-mcp serve`.
Only four tools are registered: `mt5_data_status`, `mt5_symbol_spec`, `mt5_quote`
and `mt5_capture_bars`. There are no account resources, order tools, Python/shell
execution, chart EAs, sockets, credential configuration or execution switches.

The native backend facade denies everything except bound-terminal lifecycle and
explicit market-data reads. It checks demo mode, terminal installation and
in-process account continuity before/after reads. It does not claim to sandbox
arbitrary host Python or to bind an account across service restarts. The owner
keeps the intended demo account logged in; startup binds the current demo
identity in private process memory. Read failures never select another terminal.

## Reproduce the environment

Use existing compatible native Windows Python and the reviewed uv. The local
runtime is `artifacts/mt5-mcp-runtime`; it is separate from `.venv`. Clone the
approved candidate into ignored storage, checkout its exact commit, verify a
clean checkout and the canonical upstream lock, then export with:

```powershell
uv export --project <pinned-checkout> --frozen --no-dev --no-emit-project --format requirements-txt --output-file <requirements.txt>
uv venv <isolated-runtime> --python <existing-python.exe>
uv pip sync --python <isolated-runtime>/Scripts/python.exe --require-hashes --only-binary :all: <requirements.txt>
```

Verify the candidate wheel's SHA-256 and compare all package Python files against
the approved checkout (account for Windows checkout line endings). Install the
verified wheel with `uv pip install --no-deps --no-index`; do not resolve its
runtime version ranges. Inspect the exact native closure and preserve license
notices before executing it. See the review for the accepted wheel hash and
limits of dependency checks. Do not install Linux/RPyC bridge extras or run any
upstream install scripts. No interpreter is downloaded automatically here.

## Start and verify

Set `PYTHONPATH` to this checkout's absolute `src` directory. The terminal must
already be open and logged into the intended demo account; no credentials are
accepted by the entry point. Commands use the isolated runtime's Python:

```powershell
<runtime-python.exe> -m fxra.mt5_readonly --terminal <absolute-terminal64.exe> --captures <absolute-quarantine-dir>
<runtime-python.exe> infra/mt5-mcp/probe.py --fake --evidence artifacts/mt5-mcp-review/protocol-fake.json
<runtime-python.exe> infra/mt5-mcp/probe.py --terminal <absolute-terminal64.exe> --count 1000
```

The first command serves stdio, not a network endpoint. The probe spawns that
same entry point, verifies tools/resources, rejects forged mutation calls and
invalid counts, then reads status, specification, quote and closed M1 bars. Keep
each capture, even if its quality is insufficient; no backtest is triggered.

Register `mt5-data` in the user's Codex configuration using the isolated Python,
`-m fxra.mt5_readonly`, absolute terminal/capture paths and `PYTHONPATH`. An
`enabled_tools` allowlist of the four names is defense in depth; enforcement is
in the service. Preserve unrelated MCP settings and a private pre-edit backup.
Restart Codex to load the new tool set, then verify actual tool calls. Registration
and a standalone protocol probe do not prove the current chat loaded the tools.

Captures retain original epochs and checksums under `data/mt5-quarantine/` and
are explicitly `research_ready: false`. They are not immutable raw-store
partitions, a verified `MarketDataAdapter`, a LEAN export, or a historical cost
model. Follow [the workflow](../../docs/mt5-lean-workflow.md) before ingestion.

Rollback: remove only the `mt5-data` entry from Codex config (or restore its
private backup after checking for later edits). Keep captures and run records.
Do not toggle Algo Trading or loosen security/risk settings to read data.
