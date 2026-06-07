from __future__ import annotations

import pytest
from src.storage.models import (
    ExcursionTracker,
    FillRecord,
    OrderEventRecord,
    OrderEventType,
    OrderRole,
    Side,
    TradeRecord,
)

from conftest import make_candidate, make_trade


def test_trade_record_roundtrip():
    t = make_trade(features_snapshot={"rsi": 55}, strategy_params_snapshot={"a": 1})
    row = t.to_row()
    # enums become their string value; dicts become JSON strings
    assert row["side"] == "LONG"
    assert row["exit_reason"] == "HIT_TP2"
    assert isinstance(row["features_snapshot"], str)
    back = TradeRecord.from_row(row)
    assert back.side == Side.LONG
    assert back.features_snapshot == {"rsi": 55}
    assert back.strategy_params_snapshot == {"a": 1}


def test_trade_record_missing_required_raises():
    with pytest.raises(ValueError):
        make_trade(pnl_r=None)


def test_candidate_required():
    make_candidate()  # ok
    with pytest.raises(ValueError):
        make_candidate(reject_reason=None)


def test_order_and_fill_required():
    OrderEventRecord(
        event_id="e1", ts="2026-06-01T00:00:00Z", trade_id="t1",
        order_role=OrderRole.SL, event_type=OrderEventType.SUBMIT,
    )
    with pytest.raises(ValueError):
        OrderEventRecord(event_id="e1", ts="x", trade_id=None,
                         order_role=OrderRole.SL, event_type=OrderEventType.SUBMIT)
    FillRecord(
        fill_id="f1", ts="2026-06-01T00:00:00Z", trade_id="t1", order_id="o1",
        side=Side.LONG, price=100.0, size=0.01,
    )


def test_excursion_long():
    tr = ExcursionTracker(side=Side.LONG, entry_price=100.0, initial_sl=98.0)
    for p in (100.0, 105.0, 97.0, 101.0):
        tr.update(p)
    assert tr.mfe_price == 5.0          # high 105 - entry 100
    assert tr.mae_price == 3.0          # entry 100 - low 97
    assert tr.mfe_r == pytest.approx(5.0 / 2.0)
    assert tr.mae_r == pytest.approx(3.0 / 2.0)
    assert tr.mfe_pct == pytest.approx(0.05)


def test_excursion_short():
    tr = ExcursionTracker(side=Side.SHORT, entry_price=100.0, initial_sl=102.0)
    for p in (100.0, 95.0, 103.0):
        tr.update(p)
    assert tr.mfe_price == 5.0          # entry 100 - low 95
    assert tr.mae_price == 3.0          # high 103 - entry 100
    assert tr.mfe_r == pytest.approx(5.0 / 2.0)
    assert tr.mae_r == pytest.approx(3.0 / 2.0)
