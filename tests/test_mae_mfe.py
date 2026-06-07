from __future__ import annotations

import pytest
from src.storage.models import Side

from conftest import make_trade


def test_mae_mfe_long_via_store(store):
    store.open_trade(trade_id="t1", side=Side.LONG, entry_price=100.0,
                     contracts=0.01, initial_sl=98.0)
    for p in (100.0, 104.0, 96.0, 101.0):
        store.update_excursion("t1", p)
    rec = make_trade(trade_id="t1", side=Side.LONG, entry_price=100.0, initial_sl=98.0)
    store.close_trade(rec)
    assert rec.mfe_price == 4.0
    assert rec.mae_price == 4.0
    assert rec.mfe_r == pytest.approx(2.0)   # 4 / |100-98|
    assert rec.mae_r == pytest.approx(2.0)
    assert rec.mfe_pct == pytest.approx(0.04)


def test_mae_mfe_short_via_store(store):
    store.open_trade(trade_id="t2", side=Side.SHORT, entry_price=100.0,
                     contracts=0.01, initial_sl=102.0)
    for p in (100.0, 94.0, 103.0):
        store.update_excursion("t2", p)
    rec = make_trade(trade_id="t2", side=Side.SHORT, entry_price=100.0, initial_sl=102.0)
    store.close_trade(rec)
    assert rec.mfe_price == 6.0   # entry - low (100-94)
    assert rec.mae_price == 3.0   # high - entry (103-100)
    assert rec.mfe_r == pytest.approx(3.0)
    assert rec.mae_r == pytest.approx(1.5)
