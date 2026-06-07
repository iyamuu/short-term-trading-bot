"""SQLite in-flight state + transactional log outbox.

State (open position / orders / excursion / risk session) is short-term and lives in
SQLite. Finalized logs are NOT written to parquet here — instead, ``close_trade`` and
``record_*`` enqueue the record into ``log_outbox`` inside the SAME transaction that
mutates state. A later ``outbox.flush`` drains the outbox to parquet idempotently. This
removes the "SQLite says closed but parquet write was lost" failure mode.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    CandidateRecord,
    ExcursionTracker,
    FillRecord,
    OrderEventRecord,
    Side,
    TradeRecord,
)

_SCHEMA = Path(__file__).with_name("schema.sql")


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class StateStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --------------------------------------------------------------- open / orders
    def open_trade(
        self,
        *,
        trade_id: str,
        side: Side | str,
        entry_price: float,
        contracts: float,
        initial_sl: float,
        signal_id: str | None = None,
        entry_time: str | None = None,
        initial_tp1: float | None = None,
        initial_tp2: float | None = None,
        regime: str | None = None,
        setup_name: str | None = None,
        confidence: float | None = None,
        ml_score: float | None = None,
        risk_size_multiplier: float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        side_val = side.value if isinstance(side, Side) else side
        entry_time = entry_time or _utcnow()
        with self.conn:
            self.conn.execute(
                """INSERT INTO open_position
                   (trade_id, signal_id, side, entry_time, entry_price, contracts,
                    initial_sl, initial_tp1, initial_tp2, final_sl, regime, setup_name,
                    confidence, ml_score, risk_size_multiplier, payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade_id, signal_id, side_val, entry_time, entry_price, contracts,
                    initial_sl, initial_tp1, initial_tp2, initial_sl, regime, setup_name,
                    confidence, ml_score, risk_size_multiplier,
                    json.dumps(payload or {}),
                ),
            )
            self.conn.execute(
                """INSERT INTO mae_mfe_track
                   (trade_id, side, entry_price, initial_sl, running_high, running_low, seen)
                   VALUES (?,?,?,?,?,?,0)""",
                (trade_id, side_val, entry_price, initial_sl, entry_price, entry_price),
            )

    def attach_orders(
        self, trade_id: str, orders: list[dict[str, Any]]
    ) -> None:
        """orders: list of {order_role, order_id, client_oid, reduce_only, status}."""
        with self.conn:
            for o in orders:
                self.conn.execute(
                    """INSERT INTO open_orders
                       (order_role, trade_id, order_id, client_oid, reduce_only, status)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(trade_id, order_role) DO UPDATE SET
                         order_id=excluded.order_id,
                         client_oid=excluded.client_oid,
                         reduce_only=excluded.reduce_only,
                         status=excluded.status""",
                    (
                        o["order_role"], trade_id, o.get("order_id"), o.get("client_oid"),
                        int(bool(o.get("reduce_only", False))), o.get("status"),
                    ),
                )

    def get_open_position(self, trade_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM open_position WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_open_orders(self, trade_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM open_orders WHERE trade_id = ?", (trade_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ excursion
    def update_excursion(self, trade_id: str, price: float) -> None:
        with self.conn:
            row = self.conn.execute(
                "SELECT running_high, running_low, seen FROM mae_mfe_track WHERE trade_id=?",
                (trade_id,),
            ).fetchone()
            if row is None:
                return
            if not row["seen"]:
                high, low = price, price
            else:
                high = max(row["running_high"], price)
                low = min(row["running_low"], price)
            self.conn.execute(
                "UPDATE mae_mfe_track SET running_high=?, running_low=?, seen=1 WHERE trade_id=?",
                (high, low, trade_id),
            )

    def _excursion_tracker(self, trade_id: str) -> ExcursionTracker | None:
        row = self.conn.execute(
            "SELECT * FROM mae_mfe_track WHERE trade_id=?", (trade_id,)
        ).fetchone()
        if row is None:
            return None
        tracker = ExcursionTracker(
            side=Side(row["side"]),
            entry_price=row["entry_price"],
            initial_sl=row["initial_sl"],
            running_high=row["running_high"],
            running_low=row["running_low"],
        )
        tracker._seen = bool(row["seen"])
        return tracker

    # --------------------------------------------------------------- outbox enqueue
    def _enqueue(self, outbox_id: str, kind: str, row: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO log_outbox
               (outbox_id, kind, payload_json, created_at, flushed_at)
               VALUES (?,?,?,?,NULL)""",
            (outbox_id, kind, json.dumps(row), _utcnow()),
        )

    def record_order_event(self, record: OrderEventRecord) -> None:
        assert record.event_id is not None  # guaranteed by _required validation
        with self.conn:
            self._enqueue(record.event_id, "ORDER", record.to_row())

    def record_fill(self, record: FillRecord) -> None:
        assert record.fill_id is not None
        with self.conn:
            self._enqueue(record.fill_id, "FILL", record.to_row())

    def record_candidate(self, record: CandidateRecord) -> None:
        # Candidates never touch open-position state.
        assert record.candidate_id is not None
        with self.conn:
            self._enqueue(record.candidate_id, "CANDIDATE", record.to_row())

    # ----------------------------------------------------------------- close trade
    def close_trade(self, record: TradeRecord) -> None:
        """Finalize a trade atomically: fill MAE/MFE, enqueue summary, clear state.

        Everything below runs in one SQLite transaction, so we can never end up with
        state cleared but the summary missing (or vice versa).
        """
        assert record.trade_id is not None
        with self.conn:
            tracker = self._excursion_tracker(record.trade_id)
            if tracker is not None:
                record.apply_excursion(tracker)
            self._enqueue(record.trade_id, "TRADE", record.to_row())
            self.conn.execute("DELETE FROM open_orders WHERE trade_id=?", (record.trade_id,))
            self.conn.execute("DELETE FROM mae_mfe_track WHERE trade_id=?", (record.trade_id,))
            self.conn.execute("DELETE FROM open_position WHERE trade_id=?", (record.trade_id,))

    # ----------------------------------------------------------------- outbox read
    def pending_outbox(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM log_outbox WHERE flushed_at IS NULL ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_flushed(self, outbox_ids: list[str], marker: str | None = None) -> None:
        marker = marker or _utcnow()
        with self.conn:
            self.conn.executemany(
                "UPDATE log_outbox SET flushed_at=? WHERE outbox_id=?",
                [(marker, oid) for oid in outbox_ids],
            )
