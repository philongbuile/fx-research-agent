"""Immutable raw data store: Parquet partitions plus a manifest each.

Layout (Hive style, one partition per UTC day):

    <root>/<dataset>/source=<src>/symbol=<SYM>/resolution=<res>/date=YYYY-MM-DD/
        data.parquet
        _manifest.json

Guarantees:
- A finished partition is never rewritten. Writing to an existing partition raises.
- Writes go to a temp dir and are renamed into place, so readers never see half a partition.
- An exclusive `<partition>.lock` file stops two writers (two processes, or two machines
  on one shared disk) from writing the same partition at the same time.
  ponytail: O_EXCL lock files are not reliable on every network filesystem (some SMB/NFS
  setups). The operating rule in docs/data-contracts.md is one writer host per source;
  the lock is the second line of defense, not the first.
"""

import hashlib
import json
import os
import re
import shutil
import socket
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .contracts import SCHEMAS, Resolution
from .runs import git_info, sha256_file

MANIFEST = "_manifest.json"
DATA = "data.parquet"
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


class PartitionExists(FileExistsError):
    pass


class PartitionLocked(FileExistsError):
    pass


def partition_dir(root: Path, dataset: str, source: str, symbol: str, resolution: Resolution, day: date) -> Path:
    for name, v in (("dataset", dataset), ("source", source), ("symbol", symbol)):
        if not _SAFE.match(v):
            raise ValueError(f"{name} {v!r} must match {_SAFE.pattern}")
    if dataset not in SCHEMAS:
        raise ValueError(f"unknown dataset {dataset}")
    return (Path(root) / dataset / f"source={source}" / f"symbol={symbol}"
            / f"resolution={Resolution(resolution).value}" / f"date={day.isoformat()}")


def write_partition(root: Path, dataset: str, source: str, symbol: str, resolution: Resolution, day: date,
                    table: pa.Table, adapter: dict, provenance: dict) -> Path:
    final = partition_dir(root, dataset, source, symbol, resolution, day)
    if final.exists():
        raise PartitionExists(f"{final} exists; raw partitions are immutable")

    schema = SCHEMAS[dataset]
    table = table.cast(schema)  # raises on missing/incompatible columns
    _check_rows(table, day)

    final.parent.mkdir(parents=True, exist_ok=True)
    lock = final.with_name(final.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PartitionLocked(f"{lock} held by another writer: {lock.read_text(errors='replace')}") from None
    tmp = final.with_name(f"{final.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        os.write(fd, json.dumps({"host": socket.gethostname(), "pid": os.getpid(),
                                 "at": datetime.now(timezone.utc).isoformat()}).encode())
        os.close(fd)
        tmp.mkdir()
        pq.write_table(table, tmp / DATA, compression="zstd")
        times = table.column("time" if "time" in table.column_names else "observed_at")
        manifest = {
            "manifest_version": 1,
            "dataset": dataset, "source": source, "symbol": symbol,
            "resolution": Resolution(resolution).value, "date": day.isoformat(),
            "file": DATA, "sha256": sha256_file(tmp / DATA), "bytes": (tmp / DATA).stat().st_size,
            "row_count": table.num_rows,
            "time_min": pc.min(times).as_py().isoformat() if table.num_rows else None,
            "time_max": pc.max(times).as_py().isoformat() if table.num_rows else None,
            "schema": str(schema),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "writer": {"host": socket.gethostname(), "pid": os.getpid()},
            "adapter": adapter,
            "provenance": provenance,
            "code": git_info(),
        }
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True))
        if final.exists():  # lost a race with a writer that did not use the lock
            raise PartitionExists(f"{final} appeared during write")
        tmp.rename(final)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
        lock.unlink(missing_ok=True)
    return final


def _check_rows(table: pa.Table, day: date) -> None:
    col = "time" if "time" in table.column_names else "observed_at"
    times = table.column(col).to_pylist()
    if any(t is None for t in times):
        raise ValueError(f"null {col}")
    if times != sorted(times) or len(set(times)) != len(times):
        raise ValueError(f"{col} must be strictly increasing (sorted, no duplicates)")
    wrong = [t for t in times if t.astimezone(timezone.utc).date() != day]
    if wrong:
        raise ValueError(f"{len(wrong)} rows outside UTC day {day}, first {wrong[0].isoformat()}")


def verify(root: Path) -> list[str]:
    """Recompute checksums and row counts for every partition. Returns problems; empty = clean."""
    problems = []
    for m in sorted(Path(root).rglob(MANIFEST)):
        meta = json.loads(m.read_text())
        f = m.parent / meta["file"]
        if not f.exists():
            problems.append(f"{m.parent}: missing {meta['file']}")
            continue
        if sha256_file(f) != meta["sha256"]:
            problems.append(f"{m.parent}: sha256 mismatch")
            continue  # contents untrusted; do not parse
        if pq.ParquetFile(f).metadata.num_rows != meta["row_count"]:
            problems.append(f"{m.parent}: row_count mismatch")
    for leftover in Path(root).rglob("*.lock"):
        problems.append(f"{leftover}: stale lock (writer crashed or still running)")
    for leftover in Path(root).rglob("*.tmp-*"):
        problems.append(f"{leftover}: leftover temp dir")
    return problems


def manifest_ref(partition: Path) -> dict:
    """Pointer a run record stores: path plus manifest checksum."""
    m = Path(partition) / MANIFEST
    return {"path": Path(partition).as_posix(), "manifest_sha256": hashlib.sha256(m.read_bytes()).hexdigest()}
