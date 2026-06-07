"""Date-partitioned, idempotent parquet append primitive.

Layout: ``<base_dir>/<name>/dt=YYYY-MM-DD/data.parquet`` — one file per UTC date,
rewritten on append. Idempotency is keyed on a per-record id column: re-appending a
row whose id already exists in its partition is a no-op. This is what makes the outbox
flush safe to retry.

Volumes here are small (one row per trade / order event / fill / candidate), so the
read-merge-rewrite-per-partition strategy is more than adequate and keeps the dedup
logic trivial.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd


def _date_of(ts: Any) -> str:
    """UTC date partition key from an ISO-ish timestamp string."""
    s = str(ts)
    return s[:10]


def append_rows(
    base_dir: str | os.PathLike[str],
    name: str,
    rows: list[dict[str, Any]],
    *,
    id_field: str,
    ts_field: str,
) -> int:
    """Append ``rows`` to the ``name`` dataset, deduped by ``id_field``.

    Returns the number of rows actually written (after dedup).
    """
    if not rows:
        return 0

    # Dedup within this batch first (idempotency must hold for duplicates passed in a
    # single call, not only against already-persisted rows). Keep first occurrence.
    seen_batch: set[str] = set()
    by_date: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        rid = str(r[id_field])
        if rid in seen_batch:
            continue
        seen_batch.add(rid)
        by_date.setdefault(_date_of(r[ts_field]), []).append(r)

    written = 0
    for dt, date_rows in by_date.items():
        part_dir = Path(base_dir) / name / f"dt={dt}"
        part_dir.mkdir(parents=True, exist_ok=True)
        path = part_dir / "data.parquet"

        if path.exists():
            existing = pd.read_parquet(path)
            existing_ids = set(existing[id_field].astype(str)) if id_field in existing else set()
        else:
            existing = None
            existing_ids = set()

        fresh = [r for r in date_rows if str(r[id_field]) not in existing_ids]
        if not fresh:
            continue

        new_df = pd.DataFrame(fresh)
        if existing is not None:
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df
        combined.to_parquet(path, index=False)
        written += len(fresh)

    return written


def dataset_path(base_dir: str | os.PathLike[str], name: str) -> Path:
    return Path(base_dir) / name
