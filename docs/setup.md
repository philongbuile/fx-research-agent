# Setup

## Both dev machines

1. Install Git, Docker (Docker Desktop on Windows/macOS, Docker Engine on Linux) and [uv](https://docs.astral.sh/uv/).
2. Clone the repo and run `uv sync`, then `uv run pytest`.
3. Pull the pinned LEAN image: `docker pull quantconnect/lean:18206`. It is about 14 GB to download and **42.5 GB on disk**. Between research sessions you can remove it with `docker image rm quantconnect/lean:18206` and pull it again later; the tag is pinned, so you get the same engine back.
4. Run the smoke test: `uv run fxra backtest lean/TechnicalSmokeTest/research.toml`. You should see `"status": "completed"` and one order. The run is in `runs/<run_id>/`.
5. Connect to the shared Hindsight server: `infra/hindsight/README.md`, section 3.

## LEAN

- We run the engine image directly (`src/fxra/lean.py`), with no LEAN CLI and no QuantConnect account (D-07). The command is the same as the one the CLI generates: mount the algorithm at `/LeanCLI`, mount results at `/Results`, and run `QuantConnect.Lean.Launcher.dll` in backtesting mode with Python.
- New algorithm: create `lean/<Name>/main.py` with one `QCAlgorithm` subclass, plus `research.toml` (copy `config/research.example.toml`).
- Data: without `--data-dir`, LEAN uses the sample data inside the image (Oanda forex sample, EURUSD minute data for 2014-05 only). Real data comes from the raw store through `uv run fxra export-lean --source S --symbol SYM --resolution minute --start D --end D`, then `--data-dir data/lean`. Each `<type>/<market>` folder of the export (e.g. `forex/oanda`) is mounted over the image's copy; the image's market-hours and symbol-properties databases stay in use, so the session calendar is OANDA's, not the broker's. The run record copies the export's `manifest_refs` into `data.manifests` and stores the sha256 of `export.json`.
- If you want the LEAN CLI for its research notebooks: `pip install lean`. It needs a QuantConnect organization on a paid tier.

## MetaTrader 5 (later, Windows only)

Not used in Phase 0. When Q-01 is answered: install the broker's MT5 terminal, open a **demo** account, and run `uv add MetaTrader5` (it has only `win_amd64` wheels). Ingestion then runs on that Windows machine only (single writer, D-09).

## Docker host (Hindsight)

See `infra/hindsight/README.md`.
