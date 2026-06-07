"""Rejected entry candidates -> parquet (idempotent on ``candidate_id``).

Captures *why* an entry did not happen (reject_stage + reject_reason + detail), which
is the raw material for risk / ML / no-trade analysis downstream.
"""

from __future__ import annotations

import os

from .models import CandidateRecord
from .parquet_writer import append_rows

NAME = "candidate_log"


def append(base_dir: str | os.PathLike[str], records: list[CandidateRecord]) -> int:
    rows = [r.to_row() for r in records]
    return append_rows(base_dir, NAME, rows, id_field="candidate_id", ts_field="ts")
