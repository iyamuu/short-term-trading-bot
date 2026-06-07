"""Finalized trade summaries -> parquet (idempotent on ``trade_id``)."""

from __future__ import annotations

import os

from .models import TradeRecord
from .parquet_writer import append_rows

NAME = "trade_log"


def append(base_dir: str | os.PathLike[str], records: list[TradeRecord]) -> int:
    rows = [r.to_row() for r in records]
    return append_rows(base_dir, NAME, rows, id_field="trade_id", ts_field="exit_time")
