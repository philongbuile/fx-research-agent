"""Run a LEAN backtest in the official quantconnect/lean Docker image, without the LEAN CLI.

Why not the CLI: QuantConnect's docs say CLI use needs a paid-tier organization
(docs/decisions.md D-07). The engine itself (Apache-2.0) runs fine from the image.
The image tag is pinned so a run can be reproduced; change it through a reviewed commit.
"""

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
             data_dir: Path | None = None) -> int:
    """Run main.py in project_dir. Results land in results_dir. Returns the container exit code.

    data_dir None = the sample data bundled in the image (/Lean/Data). Otherwise mount a
    LEAN-format data folder read-only in its place.
    """
    project_dir, results_dir = Path(project_dir).resolve(), Path(results_dir).resolve()
    if not (project_dir / "main.py").exists():
        raise FileNotFoundError(project_dir / "main.py")
    results_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["docker", "run", "--rm", "--network", "none",  # backtests need no network
           "-v", f"{project_dir}:/LeanCLI:ro", "-v", f"{results_dir}:/Results"]
    if data_dir:
        cmd += ["-v", f"{Path(data_dir).resolve()}:/Lean/Data:ro"]
    cmd += ["-w", "/Lean/Launcher/bin/Debug", "--entrypoint", "dotnet", image,
            "QuantConnect.Lean.Launcher.dll",
            "--environment", "backtesting",
            "--algorithm-language", "Python",
            "--algorithm-type-name", "main",
            "--algorithm-location", "/LeanCLI/main.py",
            "--data-folder", "/Lean/Data",
            "--results-destination-folder", "/Results",
            "--close-automatically", "true"]
    with open(results_dir / "engine.log", "w") as log:
        return subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
