# fx-research-agent

Open-source workbench for **forex research**, with LLM coding agents (Claude, Codex) as assistants. It does not predict prices. It does not trade on its own.

Goals:

- Collect market data with clear provenance: where it came from, when, which code, and which checksum.
- Research strategy hypotheses with QuantConnect LEAN. Backtest them, stress-test them, and keep every run reproducible.
- Remember hypotheses, decisions, results and postmortems across sessions and machines, in a shared [Hindsight](https://github.com/vectorize-io/hindsight) memory server.
- Run paper or demo trading before anyone considers live trading. Live trading needs owner approval, and is out of scope for now.

> **Status: Phase 0 (scaffold).** No broker, pair, timeframe or strategy is chosen. Nothing in this repository can send an order to a broker.

## Components

| Component | Role | State |
|---|---|---|
| [QuantConnect LEAN](https://github.com/QuantConnect/Lean) (Apache-2.0) | Strategy code, backtest, paper trading, statistics | Runs from the pinned Docker image `quantconnect/lean:18206` |
| `fxra` (this repo, Python) | Data contracts, immutable raw store, run records, risk gate, adapter interfaces | Phase 0 |
| [Hindsight](https://github.com/vectorize-io/hindsight) (MIT) | Shared memory over MCP/API: hypotheses, decisions, results, lessons | Docker Compose in `infra/hindsight` |
| MetaTrader 5 | Planned data/execution source, if the broker uses MT5 | Not integrated. No LEAN–MT5 adapter exists here (see `docs/decisions.md`) |

Read next: [architecture](docs/architecture.md), [decisions](docs/decisions.md), [research lifecycle](docs/research-lifecycle.md), [data contracts](docs/data-contracts.md), [security](docs/security.md), [risks](docs/risks.md), [backlog](docs/backlog.md), [setup](docs/setup.md).

## Quick start

You need Docker, Git, and [uv](https://docs.astral.sh/uv/) (Python 3.12 or later).

```bash
git clone https://github.com/philongbuile/fx-research-agent.git
cd fx-research-agent
uv sync
uv run pytest
```

Run the LEAN engine smoke test. The first run pulls a large image (about 14 GB compressed):

```bash
docker pull quantconnect/lean:18206
uv run fxra backtest lean/TechnicalSmokeTest/research.toml
```

More run commands (details in `docs/research-lifecycle.md`):

```bash
uv run fxra backtest <config> --segment out_of_sample   # once per hypothesis
uv run fxra walk-forward <config>                        # needs [walk_forward] and committed code
uv run fxra stress <config> --spread 1.5 --spread 2 --slippage-points 2
uv run fxra report --runs-root runs                      # markdown comparison of all runs
```

`lean/TechnicalSmokeTest` only checks that the engine works. It is **not a strategy and has no edge**.

Dry-run one order intent through the risk gate. With the example limits, the gate rejects it, because empty allowlists fail closed:

```bash
cp config/risk.example.toml config/risk.toml
uv run fxra risk-check --limits config/risk.toml --request config/risk-request.example.json
```

To start the shared memory server and connect both machines, see [`infra/hindsight/README.md`](infra/hindsight/README.md).

## Repository layout

```
AGENTS.md / CLAUDE.md   rules for coding agents (AGENTS.md is canonical)
config/                 example configs (copy, then edit; real copies are gitignored)
docs/                   architecture, decisions, lifecycle, contracts, security, risks, backlog, setup
experiments/            one folder per hypothesis: hypothesis.md, research.toml, notes
infra/hindsight/        Docker Compose and runbook for the shared memory server
lean/                   LEAN algorithms (one folder per algorithm, main.py)
reports/                reviewed write-ups of runs (small, committed)
schemas/                JSON Schemas (run record)
src/fxra/               contracts, adapters, raw store, run records, risk gate, LEAN runner, CLI
tests/                  pytest
data/ runs/ artifacts/  generated, gitignored
```

## Ground rules

- Market data, spreads, swaps, account state, orders and computed numbers live in the structured store (`data/`, `runs/`). They do not live in Hindsight. Hindsight holds the narrative: why a run happened, what was learned, and what was decided.
- An LLM may propose or review a candidate strategy. It may never edit, promote or deploy a live strategy, and it never gets an order-sending tool.
- No secrets, credentials, account identifiers or bulk broker data in git.

## License

Apache-2.0 (proposed; see `docs/decisions.md` Q-07).
