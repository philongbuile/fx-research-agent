"""Run a LEAN backtest in the official quantconnect/lean Docker image, without the LEAN CLI.

Why not the CLI: QuantConnect's docs say CLI use needs a paid-tier organization
(docs/decisions.md D-07). The engine itself (Apache-2.0) runs fine from the image.
The image tag is pinned so a run can be reproduced; change it through a reviewed commit.
"""

import json
import subprocess
from pathlib import Path

LEAN_IMAGE = "quantconnect/lean:18206"


def image_ref(image: str = LEAN_IMAGE) -> str:
    """Tag plus content digest of the local image, so the run record names the exact engine build."""
    out = subprocess.run(["docker", "image", "inspect", image, "--format", "{{index .RepoDigests 0}}"],
                         capture_output=True, text=True)
    digest = out.stdout.strip().partition("@")[2]
    return f"{image}@{digest}" if out.returncode == 0 and digest else image


def backtest(project_dir: Path, results_dir: Path, image: str = LEAN_IMAGE,
             data_dir: Path | None = None, parameters: dict | None = None) -> int:
    """Run main.py in project_dir. Results land in results_dir. Returns the container exit code.

    data_dir None = the sample data bundled in the image (/Lean/Data). Otherwise each
    <security type>/<market> folder of data_dir (e.g. forex/oanda from `fxra export-lean`) is
    mounted read-only over the image's copy. The image's market-hours and symbol-properties
    databases stay visible; mounting all of /Lean/Data would hide them.

    parameters reach the algorithm as self.get_parameter(name): written to a config file that
    the launcher merges over its default config.json (`--config`). `--parameters` on the command
    line did not work (checked 2026-10-26, see docs/decisions.md D-18).
    """
    project_dir, results_dir = Path(project_dir).resolve(), Path(results_dir).resolve()
    if not (project_dir / "main.py").exists():
        raise FileNotFoundError(project_dir / "main.py")
    results_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["docker", "run", "--rm", "--network", "none",  # backtests need no network
           "-v", f"{project_dir}:/LeanCLI:ro", "-v", f"{results_dir}:/Results"]
    if data_dir:
        markets = sorted(p for p in Path(data_dir).resolve().glob("*/*") if p.is_dir())
        if not markets:
            raise FileNotFoundError(f"{data_dir} has no <security type>/<market> folders")
        for m in markets:
            cmd += ["-v", f"{m}:/Lean/Data/{m.parent.name}/{m.name}:ro"]
    if parameters:
        extra = results_dir / "lean-config.json"  # kept with the run as an artifact
        extra.write_text(json.dumps({"parameters": {k: str(v) for k, v in parameters.items()}}, indent=2))
        cmd += ["-v", f"{extra}:/Config/lean-config.json:ro"]
    cmd += ["-w", "/Lean/Launcher/bin/Debug", "--entrypoint", "dotnet", image,
            "QuantConnect.Lean.Launcher.dll",
            "--environment", "backtesting",
            "--algorithm-language", "Python",
            "--algorithm-type-name", "main",
            "--algorithm-location", "/LeanCLI/main.py",
            "--data-folder", "/Lean/Data",
            "--results-destination-folder", "/Results",
            "--close-automatically", "true"]
    if parameters:
        cmd += ["--config", "/Config/lean-config.json"]
    with open(results_dir / "engine.log", "w") as log:
        return subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
