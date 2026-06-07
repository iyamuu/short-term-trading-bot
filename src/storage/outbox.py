"""Drain the SQLite log outbox to parquet — idempotently, with a JSONL fallback.

``flush`` reads unflushed outbox rows, groups them by kind, and appends to the matching
parquet dataset. Parquet append is itself deduped by record id, and successfully
flushed rows get ``flushed_at`` stamped, so re-running ``flush`` is a no-op (no
duplicates). If a kind's parquet write keeps failing, the rows are spilled to an
append-only JSONL fallback and left unflushed so the next run retries; the alert flag
is raised. ``reintegrate_fallback`` merges any spilled rows back into parquet, deduped.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .parquet_writer import append_rows
from .state_store import StateStore

# kind -> (dataset name, id field, timestamp field)
_KIND_MAP: dict[str, tuple[str, str, str]] = {
    "TRADE": ("trade_log", "trade_id", "exit_time"),
    "ORDER": ("order_event_log", "event_id", "ts"),
    "FILL": ("fill_log", "fill_id", "ts"),
    "CANDIDATE": ("candidate_log", "candidate_id", "ts"),
}


class FlushResult:
    def __init__(self) -> None:
        self.written: dict[str, int] = {}
        self.failed_kinds: list[str] = []
        self.spilled: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed_kinds

    @property
    def total_written(self) -> int:
        return sum(self.written.values())


def _fallback_path(base_dir: str | os.PathLike[str], kind: str) -> Path:
    p = Path(base_dir) / "fallback"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{kind.lower()}.jsonl"


def flush(store: StateStore, base_dir: str | os.PathLike[str]) -> FlushResult:
    result = FlushResult()
    pending = store.pending_outbox()
    if not pending:
        return result

    by_kind: dict[str, list[dict[str, Any]]] = {}
    ids_by_kind: dict[str, list[str]] = {}
    for entry in pending:
        kind = entry["kind"]
        by_kind.setdefault(kind, []).append(json.loads(entry["payload_json"]))
        ids_by_kind.setdefault(kind, []).append(entry["outbox_id"])

    for kind, rows in by_kind.items():
        name, id_field, ts_field = _KIND_MAP[kind]
        try:
            written = append_rows(base_dir, name, rows, id_field=id_field, ts_field=ts_field)
            result.written[kind] = written
            store.mark_flushed(ids_by_kind[kind])
        except Exception:
            # Persistent parquet failure: spill to JSONL, leave unflushed for retry.
            path = _fallback_path(base_dir, kind)
            with path.open("a", encoding="utf-8") as fh:
                for oid, row in zip(ids_by_kind[kind], rows, strict=True):
                    fh.write(json.dumps({"outbox_id": oid, "row": row}) + "\n")
            result.failed_kinds.append(kind)
            result.spilled += len(rows)

    return result


def reintegrate_fallback(base_dir: str | os.PathLike[str]) -> int:
    """Merge spilled JSONL rows back into parquet, deduped. Returns rows written."""
    written = 0
    for kind, (name, id_field, ts_field) in _KIND_MAP.items():
        path = _fallback_path(base_dir, kind)
        if not path.exists():
            continue
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            oid = obj["outbox_id"]
            if oid in seen:
                continue
            seen.add(oid)
            rows.append(obj["row"])
        if rows:
            written += append_rows(base_dir, name, rows, id_field=id_field, ts_field=ts_field)
    return written
