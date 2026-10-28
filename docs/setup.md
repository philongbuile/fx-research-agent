# Setup

## Both dev machines

1. Install Git, Docker (Docker Desktop on Windows/macOS, Docker Engine on Linux) and [uv](https://docs.astral.sh/uv/).
2. Clone the repo and run `uv sync`, then `uv run pytest`.
3. Pull the pinned LEAN image: `docker pull quantconnect/lean:18206`. It is about 14 GB to download and **42.5 GB on disk**. Between research sessions you can remove it with `docker image rm quantconnect/lean:18206` and pull it again later; the tag is pinned, so you get the same engine back.
4. Run the smoke test: `uv run fxra backtest lean/TechnicalSmokeTest/research.toml`. You should see `"status": "completed"` and one order. The run is in `runs/<run_id>/`.
5. Connect to the shared Hindsight server: `infra/hindsight/README.md`, section 3.

## LEAN

For this checkout's project-local Windows uv installation (review in
[`third-party/uv.md`](third-party/uv.md)), open PowerShell in the repository and
set the current process's tool paths before running the standard commands:

```powershell
$env:PATH = "$PWD\artifacts\tools\uv-0.12.7;$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin;$env:PATH"
$env:UV_CACHE_DIR = "$PWD\artifacts\uv-cache"
$env:UV_PYTHON_DOWNLOADS = 'never'
uv --version
uv sync --locked --python (Get-Command python).Source
uv run pytest
docker pull quantconnect/lean:18206
uv run fxra backtest lean/TechnicalSmokeTest/research.toml
```

The executable/cache and `.venv` stay ignored; no global Python, shell profile or
user PATH is changed. The local executable is not distributed through Git; a new
machine must install the reviewed tool first. Use an already installed compatible
Python. A first engine download can take time; do not treat the presence of the
Docker Desktop UI as proof that its Linux engine is responding.

Local research and engine learning can proceed while ai-memory activation and
the two-machine cutover remain incomplete. The owner deferred the two-machine
connection on 2026-10-28. This deferral does not mark either acceptance test done,
and does not make the supervised-character/replay milestones complete.

The local environment and recorded engine smoke trial passed on 2026-10-28:
see [verification evidence](local-lean-verification.md), then follow
[the first research-learning session](research-first-session.md).

- We run the engine image directly (`src/fxra/lean.py`), with no LEAN CLI and no QuantConnect account (D-07). The command is the same as the one the CLI generates: mount the algorithm at `/LeanCLI`, mount results at `/Results`, and run `QuantConnect.Lean.Launcher.dll` in backtesting mode with Python.
- New algorithm: create `lean/<Name>/main.py` with one `QCAlgorithm` subclass, plus `research.toml` (copy `config/research.example.toml`).
- Data: without `--data-dir`, LEAN uses the sample data inside the image (Oanda forex sample, EURUSD minute data for 2014-05 only). Real data comes from the raw store through `uv run fxra export-lean --source S --symbol SYM --resolution minute --start D --end D`, then `--data-dir data/lean`. Each `<type>/<market>` folder of the export (e.g. `forex/oanda`) is mounted over the image's copy; the image's market-hours and symbol-properties databases stay in use, so the session calendar is OANDA's, not the broker's. The run record copies the export's `manifest_refs` into `data.manifests` and stores the sha256 of `export.json`.
- If you want the LEAN CLI for its research notebooks: `pip install lean`. It needs a QuantConnect organization on a paid tier.

## MetaTrader 5 (later, Windows only)

Not used in Phase 0. When Q-01 is answered: install the broker's MT5 terminal, open a **demo** account, and run `uv add MetaTrader5` (it has only `win_amd64` wheels). Ingestion then runs on that Windows machine only (single writer, D-09).

## Docker host (Hindsight)

See `infra/hindsight/README.md`.
