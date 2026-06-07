from __future__ import annotations

import pandas as pd
import pytest
from src.storage import event_log
from src.storage.models import FillRecord, OrderEventRecord, OrderEventType, OrderRole, Side


def test_order_and_fill_logs_and_reconstruct_pnl(base_dir):
    events = [
        OrderEventRecord(
            event_id="e1", ts="2026-06-01T00:00:00Z", trade_id="t1",
            order_role=OrderRole.ENTRY, event_type=OrderEventType.ACK, side=Side.LONG,
            reduce_only=False, price=100.0, size=0.01,
        ),
        OrderEventRecord(
            event_id="e2", ts="2026-06-01T00:30:00Z", trade_id="t1",
            order_role=OrderRole.SL, event_type=OrderEventType.SUBMIT, side=Side.LONG,
            reduce_only=True, stop_price=99.0, trigger_type="mark_price", size=0.01,
        ),
    ]
    fills = [
        FillRecord(fill_id="f1", ts="2026-06-01T00:00:01Z", trade_id="t1", order_id="o1",
                   order_role=OrderRole.ENTRY, side=Side.LONG, price=100.0, size=0.01,
                   fee_abs=0.001),
        FillRecord(fill_id="f2", ts="2026-06-01T01:00:00Z", trade_id="t1", order_id="o2",
                   order_role=OrderRole.TP2, side=Side.SHORT, price=103.0, size=0.01,
                   fee_abs=0.001),
    ]
    assert event_log.append_order(base_dir, events) == 2
    assert event_log.append_fill(base_dir, fills) == 2

    fill_df = pd.read_parquet(next(base_dir.glob("fill_log/dt=*/*.parquet")))
    # reconstruct gross pnl & fees purely from fills
    entry = fill_df[fill_df.order_role == "ENTRY"].iloc[0]
    close = fill_df[fill_df.order_role == "TP2"].iloc[0]
    gross = (close.price - entry.price) * entry["size"]
    total_fee = float(fill_df.fee_abs.sum())
    assert gross == pytest.approx(0.03)
    assert total_fee == pytest.approx(0.002)


def test_event_log_idempotent(base_dir):
    ev = OrderEventRecord(
        event_id="e1", ts="2026-06-01T00:00:00Z", trade_id="t1",
        order_role=OrderRole.ENTRY, event_type=OrderEventType.ACK,
    )
    event_log.append_order(base_dir, [ev])
    assert event_log.append_order(base_dir, [ev]) == 0
