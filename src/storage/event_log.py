"""Order/fill facts -> parquet (idempotent on event_id / fill_id).

The trade summary's pnl/fee/slippage must be reconstructable from these rows, so they
are kept as separate append-only datasets rather than folded into the summary.
"""

from __future__ import annotations

import os

from .models import FillRecord, OrderEventRecord
from .parquet_writer import append_rows

ORDER_NAME = "order_event_log"
FILL_NAME = "fill_log"


def append_order(base_dir: str | os.PathLike[str], records: list[OrderEventRecord]) -> int:
    rows = [r.to_row() for r in records]
    return append_rows(base_dir, ORDER_NAME, rows, id_field="event_id", ts_field="ts")


def append_fill(base_dir: str | os.PathLike[str], records: list[FillRecord]) -> int:
    rows = [r.to_row() for r in records]
    return append_rows(base_dir, FILL_NAME, rows, id_field="fill_id", ts_field="ts")
