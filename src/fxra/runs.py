"""Research run records. One run = one directory holding run.json plus its artifacts.

run.json follows schemas/run.schema.json. A run whose code was dirty (uncommitted
changes) is recorded but marked not reproducible and can never be promoted.
"""

import hashlib
import json
import secrets
import shutil
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

REPO = Path(__file__).resolve().parents[2]
RUN_SCHEMA = REPO / "schemas" / "run.schema.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_info(cwd: Path = REPO) -> dict:
    def git(*args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
    try:
        return {"git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain"))}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"git_commit": None, "git_dirty": True}


def validate(record: dict) -> None:
    jsonschema.validate(record, json.loads(RUN_SCHEMA.read_text()))


def new_run(config_path: Path, runs_root: Path, engine_image: str, now: datetime | None = None,
            data_export: Path | None = None) -> Path:
    """data_export: export.json of a `fxra export-lean` folder; its manifest_refs become data.manifests."""
    config_path = Path(config_path)
    cfg = json.loads(json.dumps(tomllib.loads(config_path.read_text()), default=str))  # TOML dates -> ISO strings
    data = cfg["data"] | {"manifests": cfg["data"].get("manifests", [])}
    if data_export:  # checked before the run dir exists, so a mismatch leaves nothing behind
        exports = json.loads(Path(data_export).read_text())["exports"]
        if data["source"] not in {e["source"] for e in exports}:
            raise ValueError(f"config data.source {data['source']!r} is not in {data_export}")
        data["manifests"] = [r for e in exports for r in e["manifest_refs"]]
        data["export"] = {"path": Path(data_export).as_posix(), "sha256": sha256_file(data_export)}
    now = now or datetime.now(timezone.utc)
    run_id = f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir(parents=True)
    shutil.copy2(config_path, run_dir / "config.toml")

    code = git_info() | {"strategy_path": cfg["run"]["strategy_path"]}
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": now.isoformat(),
        "kind": cfg["run"]["kind"],
        "hypothesis_id": cfg["run"]["hypothesis_id"],
        "technical_only": cfg["run"].get("technical_only", False),
        "reproducible": bool(code["git_commit"]) and not code["git_dirty"],
        "code": code,
        "config": {"path": config_path.as_posix(), "sha256": sha256_file(config_path)},
        "engine": {"name": "lean", "image": engine_image},
        "data": data,
        "assumptions": cfg["assumptions"],
        "split": {"in_sample": cfg["split"]["in_sample"],
                  "out_of_sample": cfg["split"].get("out_of_sample")},  # TOML has no null: omit = none
        "metrics": {},
        "artifacts": [],
        "status": "created",
    }
    validate(record)
    (run_dir / "run.json").write_text(json.dumps(record, indent=2))
    return run_dir


def finalize_run(run_dir: Path, ok: bool) -> dict:
    """Pull LEAN summary statistics into run.json and list artifacts."""
    run_dir = Path(run_dir)
    record = json.loads((run_dir / "run.json").read_text())
    summaries = sorted((run_dir / "lean").glob("*-summary.json"))
    if summaries:
        s = json.loads(summaries[-1].read_text())
        record["metrics"] = {"statistics": s.get("statistics", {}),
                             "runtime_statistics": s.get("runtimeStatistics", {})}
    record["artifacts"] = sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*")
                                 if p.is_file() and p.name != "run.json")
    record["status"] = "completed" if ok and summaries else "failed"
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    validate(record)
    (run_dir / "run.json").write_text(json.dumps(record, indent=2))
    return record
