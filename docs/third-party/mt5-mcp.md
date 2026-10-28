# MT5 MCP candidate review

Reviewed 2026-10-28 for local Windows research/data access under D-28. Status:
**owner approved the exact review on 2026-10-28; pinned package and restricted
local profile installed and verified.** Native tool loading after a Codex
restart remains to be checked. See [local evidence](../local-mt5-mcp-verification.md).

## Comparison and selection

GitHub API observations on the review date; stars are discovery signals, not a
security rating. This is a bounded comparison, not proof of the most trustworthy
repository across GitHub.

| Candidate | Observed stars | Findings | Disposition |
|---|---:|---|---|
| [ariadng/metatrader-mcp-server](https://github.com/ariadng/metatrader-mcp-server) at `bd2ff591e32cb492fafefe74221d502dd78b71ef` | 840 | MIT; active tests and broad MT5 surface. MCP registers direct mutations without D-28 confirmation, CLI requires credential arguments, network default is SSE on all interfaces. Date-range function is not registered as an MCP tool. | Reject unmodified deployment despite popularity. |
| [Qoyyuum/mcp-metatrader5-server](https://github.com/Qoyyuum/mcp-metatrader5-server) | 238 | GitHub reports no recognized license; raw order sending advertised. | License and execution scope unresolved; not selected. |
| [iwanao513/mt5-mcp-server](https://github.com/iwanao513/mt5-mcp-server) at `040538406e398f87101f9793b5f4a6a155c7f08b` | 12 | MIT; primarily tester/optimization/report runner. | Different purpose from reading the logged-in terminal into LEAN. |
| [Cloudmeru/MetaTrader-5-MCP-Server](https://github.com/Cloudmeru/MetaTrader-5-MCP-Server) at `b4ded2c9806e0ce290ff7e2af6004913e71bf67e` | 3 | MIT; advertised read-only, but `mt5_execute` compiles/executes caller Python with data-science modules. A substring blocklist and Python object proxy are not a sandbox. Heavy forecasting dependencies. | Reject unmodified deployment. |
| [CodeWithBehnam/meta-trader-5-mcp](https://github.com/CodeWithBehnam/meta-trader-5-mcp) | 0 | MIT; documented bridge aimed at macOS/Wine. | Does not match native Windows deployment. |
| [vincentwongso/mt5-trading-mcp](https://github.com/vincentwongso/mt5-trading-mcp) at `daa905d36135aa1f00017efccc9b39fba7130c3f` | 6 | MIT; native Windows attach, typed tools, lockfile, tests, audit/idempotency code. Upstream execution defaults and confirmation are insufficient for D-28. | Preferred **conditional candidate for a restricted market-data-only profile**, not the stock server. |

## Exact source and supply chain

- Candidate package: `mt5-trading-mcp`, source version `1.6.1`, exact Git commit
  `daa905d36135aa1f00017efccc9b39fba7130c3f` (GitHub source metadata inspected;
  subsequently matched against the downloaded wheel; see local acceptance below).
- `uv.lock` SHA-256:
  `0d39ae11ce9867d38f52f491ab7e53d26bca8179bafb9c1e8a20bc189e776642`.
- MIT source license includes commercial use and redistribution subject to
  notice preservation, compatible with this project's Apache-2.0 distribution.
- Build backend: `hatchling>=1.27`; runtime ranges: `mcp[cli]>=1.12`,
  `pydantic>=2.6`, `watchdog>=4.0`, `platformdirs>=4.0`, `python-ulid>=3.0`,
  `MetaTrader5>=5.0.45` on Windows, `tomli>=2.0` on older Python. Optional RPyC
  `mt5linux` bridge is excluded from the proposed native installation.
- All 55 registry name/version entries in the upstream lock (including optional
  and development entries and both NumPy versions) were compared with PyPI
  metadata: no lookup errors, no absent license metadata, and every recorded
  archive hash appeared in that version's registry file list. Sanitized evidence:
  ignored `artifacts/mt5-mcp-review/dependency-metadata.json`.
- Metadata reports MIT/BSD/Apache/ISC/PSF/0BSD/Zlib/CC0 or dual-license entries,
  plus certifi's MPL-2.0. Preserve notices; MPL file-level obligations must remain
  attached to redistributed certifi files. NumPy carries bundled third-party
  notices; metadata alone does not enumerate every binary's bundled component.
  `python-dateutil` reports "Dual License" and requires checking actual notices.
- This is **not** a complete dependency/license or vulnerability audit. Before
  execution, resolve the native Windows locked closure and build requirements,
  inspect its exact wheel notices (including NumPy and MetaTrader5), verify hashes
  and source/package correspondence, and record the installed inventory. Do not
  silently resolve the version ranges or install the optional Linux bridge.

## Code and permissions reviewed

Read the pinned pyproject/license, lock metadata, server construction, config,
MT5 adapter connection/retry code, conversion functions, market tool paths,
registry, order-tool paths and consent/engine code via GitHub's content API.
No third-party code was executed. This is not a full line-by-line audit.

- Native `MT5Client` can attach to the existing terminal without copying login
  secrets. Do not pass credentials, use `doctor` output unredacted, or expose
  account/system resources: upstream models include login/name/server fields.
- Source defines an optional HTTP transport and RPyC backend. Proposed profile
  uses stdio only: no listening socket, cloud relay, telemetry or credentials in
  CLI/config/logs. No screenshots/EA deployment or streaming account resources.
- Upstream registers all tools. Its `auto_approve_notional=0` default disables
  approval; the model can supply `approval_confirmed` and a returned request ID.
  Cancellation requires no approval. This is not a trusted human authorization
  channel. Consent comparison omits some request fields such as stop/take-profit.
- Order tools route `order_send` through a generic reconnect/retry helper. An
  ambiguous result needs explicit reconciliation; an existing helper or audit
  log does not establish D-28 acceptance.
- Current-offset inference can fall back to zero and is applied to historical
  bars. It is not proof of historical UTC/DST. Preserve original epochs and
  verify the time domain before structured ingestion; do not accept inferred
  historical UTC as fact.
- No unrelated outbound call was identified in the inspected native attach/read
  paths. Optional transports/features and transitive package code have not all
  been audited; no blanket "no network" or "no telemetry" certification is made.

## Proposed installation and acceptance scope

Owner approved this candidate and the following bounded profile:

1. Install the exact verified artifact/dependency closure in an isolated native
   Windows environment. Keep the research project's dependency environment
   separate; preserve source/license evidence privately without credentials.
2. Use a first-party server entry point with explicit market-data tools only.
   Do not run/register the upstream all-tools CLI. Deny mutation calls at the
   backend boundary as well as omitting their tool registrations. No arbitrary
   Python/shell, account resources, `order_check`, `order_send`, login switching
   or execution-enabling configuration. An SDK in the service is not exposed as
   a generic callable tool. This boundary must be implemented and tested first.
3. Test tool enumeration and unknown/forged tool calls with a fake terminal;
   prove no order path is reachable and identity/credential fields are redacted.
   Bind the configured terminal; a connection failure must report an error, not
   silently select another installation/account. Verify demo mode privately.
4. Attach to the existing logged-in terminal. Retrieve a bounded EURUSD M1 sample
   and symbol metadata; save the original payload with checksum/provenance in
   ignored storage, account alias only. Verify actual MCP calls, not just config.
5. Keep timestamps quarantined until UTC/DST is verified. Only then ingest via a
   tested `MarketDataAdapter` and `rawstore.write_partition`; follow the
   [MT5/LEAN workflow](../mt5-lean-workflow.md).

No trade-confirmation service or demo execution is included. A future execution
profile must independently satisfy D-28 and reviewed risk/promotion gates.

## Approval record

The owner approved this exact review on 2026-10-28 ("approved everything, pull
main and continue") after merging PR #20. The restricted profile was then
installed, tested and registered; real MCP stdio calls retrieved demo market
data. No upstream all-tools server or execution feature was enabled.

## Local artifact acceptance

- Official PyPI wheel `mt5_trading_mcp-1.6.1-py3-none-any.whl` SHA-256:
  `abf5e187e4882a825ebc4abbdc4a226cc1e49ccb9226546a84254040b7dd2fad`.
  All 40 package Python files match the approved Git checkout after normalizing
  CRLF/LF checkout line endings. Wheel bytes were independently hash-checked;
  this comparison does not claim a reproducible-build attestation.
- Native CPython 3.13 runtime: 43 exact dependency distributions installed with
  `--require-hashes --only-binary :all:` from the frozen upstream lock export,
  plus the verified candidate wheel installed with `--no-deps --no-index`.
  No source builds, optional bridge, global interpreter install or project
  dependency changes. `uv pip check` passed.
- Installed wheel notices/digests were inventoried; MetaTrader5's bundled license
  is MIT. NumPy and certifi notices remain in the runtime. The pywin32 wheel
  declares PSF in metadata but omits standalone notice files; preserve official
  b311 source license texts at immutable commit
  `8b328dffac71b7afaf2d72f47c4048f27a32f6c8` as supplemental evidence, including
  the BSD-style Mark Hammond notice. Do not treat registry metadata as a complete
  binary license audit or redistribute an incomplete notice bundle.
- `pywin32.pth` loads the packaged bootstrap to register its DLL directory; the
  bootstrap was inspected. No upstream project install/build script was run.
- Ignored evidence: `artifact.json`, `installed-notices.json`,
  `runtime-requirements.txt`, `pywin32-notices/`, `protocol-fake.json`,
  `protocol-native.json` and `registration.json` under `artifacts/mt5-mcp-review/`.

Full binary/source security auditing and future execution acceptance are not
claimed. Captures remain quarantined pending timestamp/calendar verification.
