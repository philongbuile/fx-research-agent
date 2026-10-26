"""Immutable raw data store: Parquet batches plus a manifest each.

Layout (Hive style, one partition per UTC day, one or more batches per partition):

    <root>/<dataset>/source=<src>/symbol=<SYM>/resolution=<res>/date=YYYY-MM-DD/batch=<batch_id>/
        data.parquet
        _manifest.json

`batch_id` is `YYYYMMDDTHHMMSSZ-<8 hex>` (UTC), so batches sort by time.

Guarantees:
- A finished batch is never modified or deleted. Writing to a day that already has a batch
  raises, unless `supersede=<reason>` is given: that writes a new batch whose manifest
  names the previous one (`supersedes`). Readers use the latest batch; old ones stay for audit.
- Writes go to a temp dir and are renamed into place, so readers never see half a batch.
- An exclusive `<partition>.lock` file (one per day) stops two writers (two processes, or two
  machines on one shared disk) from writing the same day at once, so "latest" cannot race.
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .contracts import SCHEMAS, Resolution
from .runs import git_info, sha256_file

MANIFEST = "_manifest.json"
DATA = "data.parquet"
BATCH = "batch="
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


def _batch_dirs(part: Path) -> list[Path]:
    """Complete batches of a day partition, oldest first. Temp dirs live outside the partition."""
    return sorted(d for d in part.glob(BATCH + "*") if (d / MANIFEST).exists()) if part.exists() else []


def latest_batch(root: Path, dataset: str, source: str, symbol: str, resolution: Resolution, day: date) -> Path | None:
    batches = _batch_dirs(partition_dir(root, dataset, source, symbol, resolution, day))
    return batches[-1] if batches else None


def write_partition(root: Path, dataset: str, source: str, symbol: str, resolution: Resolution, day: date,
                    table: pa.Table, adapter: dict, provenance: dict, supersede: str | None = None) -> Path:
    """Write a new batch, return its dir. A day that already has data needs `supersede=<reason>`."""
    if supersede is not None and not supersede.strip():
        raise ValueError("supersede reason must be non-empty")
    part = partition_dir(root, dataset, source, symbol, resolution, day)
    if _batch_dirs(part) and supersede is None:
        raise PartitionExists(f"{part} has data; pass supersede=<reason> to write a corrected batch")

    schema = SCHEMAS[dataset]
    table = table.cast(schema)  # raises on missing/incompatible columns
    _check_rows(table, day)

    part.parent.mkdir(parents=True, exist_ok=True)
    lock = part.with_name(part.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PartitionLocked(f"{lock} held by another writer: {lock.read_text(errors='replace')}") from None
    tmp = part.with_name(f"{part.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        os.write(fd, json.dumps({"host": socket.gethostname(), "pid": os.getpid(),
                                 "at": datetime.now(timezone.utc).isoformat()}).encode())
        os.close(fd)
        prev = _batch_dirs(part)  # re-read under the lock: this is the "latest" we supersede
        prev_id = prev[-1].name[len(BATCH):] if prev else None
        if prev_id and supersede is None:
            raise PartitionExists(f"{part} appeared during write")
        now = datetime.now(timezone.utc)
        batch_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        while prev_id and batch_id <= prev_id:  # same-second supersede: keep ids strictly increasing
            now += timedelta(seconds=1)
            batch_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        tmp.mkdir()
        pq.write_table(table, tmp / DATA, compression="zstd")
        times = table.column("time" if "time" in table.column_names else "observed_at")
        manifest = {
            "manifest_version": 2,
            "dataset": dataset, "source": source, "symbol": symbol,
            "resolution": Resolution(resolution).value, "date": day.isoformat(),
            "batch_id": batch_id, "supersedes": prev_id, "supersede_reason": supersede,
            "file": DATA, "sha256": sha256_file(tmp / DATA), "bytes": (tmp / DATA).stat().st_size,
            "row_count": table.num_rows,
            "time_min": pc.min(times).as_py().isoformat() if table.num_rows else None,
            "time_max": pc.max(times).as_py().isoformat() if table.num_rows else None,
            "schema": str(schema),
            "ingested_at": now.isoformat(),
            "writer": {"host": socket.gethostname(), "pid": os.getpid()},
            "adapter": adapter,
            "provenance": provenance,
            "code": git_info(),
        }
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True))
        part.mkdir(exist_ok=True)
        final = part / f"{BATCH}{batch_id}"
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
    """Recompute checksums and row counts for every batch. Returns problems; empty = clean."""
    problems = []
    for m in sorted(Path(root).rglob(MANIFEST)):
        meta = json.loads(m.read_text())
        sup = meta.get("supersedes")
        if sup and not (m.parent.parent / f"{BATCH}{sup}" / MANIFEST).exists():
            problems.append(f"{m.parent}: supersedes missing batch {sup}")
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


def manifest_ref(batch: Path) -> dict:
    """Pointer a run record stores: batch dir path plus manifest checksum."""
    m = Path(batch) / MANIFEST
    return {"path": Path(batch).as_posix(), "manifest_sha256": hashlib.sha256(m.read_bytes()).hexdigest()}


def _read_batch(batch: Path, dataset: str) -> pa.Table:
    meta = json.loads((batch / MANIFEST).read_text())
    f = batch / meta["file"]
    if sha256_file(f) != meta["sha256"]:
        raise ValueError(f"{batch}: sha256 mismatch")
    return pq.read_table(f).cast(SCHEMAS[dataset])


def read_partition(root: Path, dataset: str, source: str, symbol: str, resolution: Resolution, day: date) -> pa.Table:
    """Latest batch of one day, checksum-verified. Raises FileNotFoundError if the day is absent."""
    b = latest_batch(root, dataset, source, symbol, resolution, day)
    if b is None:
        raise FileNotFoundError(f"no data for {dataset}/{source}/{symbol}/{Resolution(resolution).value} {day}")
    return _read_batch(b, dataset)


def read_range(root: Path, dataset: str, source: str, symbol: str, resolution: Resolution,
               start: date, end: date) -> tuple[pa.Table, list[dict]]:
    """Days in [start, end] that exist, concatenated, plus the manifest_ref of each batch read.
    Missing days are absent; nothing is filled in."""
    tables, refs = [], []
    for i in range((end - start).days + 1):
        b = latest_batch(root, dataset, source, symbol, resolution, start + timedelta(days=i))
        if b:
            tables.append(_read_batch(b, dataset))
            refs.append(manifest_ref(b))
    return (pa.concat_tables(tables) if tables else SCHEMAS[dataset].empty_table()), refs
