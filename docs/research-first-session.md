# First local research-learning session

Start after the environment checks and the pinned LEAN smoke test succeed.
The two-machine connection and ai-memory activation are separate workstreams;
neither is a prerequisite for learning the local research tools.

## Understand one recorded engine run

Run the existing technical fixture, then inspect its own outputs:

```powershell
uv run fxra backtest lean/TechnicalSmokeTest/research.toml
uv run fxra report --hypothesis TECH-0001-engine-smoke-test
```

Every invocation creates another recorded trial; keep failures. Read the exact
`runs/<run_id>/run.json`, its copied config and `lean/engine.log`. Confirm the
status, code commit, dirty flag, engine digest, dates, data source, assumptions,
segment and `technical_only` flag before discussing any result. Quote research
numbers only from that run record and cite its run ID.

The smoke algorithm deliberately places a simulated order to test engine/data
plumbing. Its container has no network. Its sample quotes, limited dates and
incomplete cost assumptions do not establish profitability or a tradable edge.
Do not treat it as a candidate strategy or promote it.

## Learn the research workflow

1. Read [data contracts](data-contracts.md), [research lifecycle](research-lifecycle.md)
   and [decisions](decisions.md). In particular, bars are stamped with open time
   while LEAN delivers them at the end of the bar (D-08).
2. Inspect the CSV importer, quality report and LEAN export through `fxra --help`
   and their tests. Real ingestion needs a verified source timezone, symbol
   contract and provenance. Do not invent broker specifications from sample data.
3. Before another backtest, write a falsifiable hypothesis using
   `experiments/_template/`. Identify the mechanism, required data, missing-data
   and weekend-gap behavior, realistic costs and failure criteria. Strategy
   selection and promotion still follow the owner's existing rules.
4. Freeze the design and data split before looking at results. Start in-sample,
   account for every variant, then use cost stress and the single permitted
   out-of-sample/walk-forward evaluation. A design change after viewing OOS
   creates a new hypothesis ID; do not tune against the held-out data.

The useful first outcome is a reproducible workflow and an honest failure
criterion. A strategy or claim of edge is a later research outcome.

For the next supervised study using MT5 demo observations, follow the
[MT5-to-LEAN workflow](mt5-lean-workflow.md). The owner-approved restricted MCP
profile has passed local protocol/data-read checks; captures still require
timestamp/calendar verification before ingestion. See the linked evidence.
