from __future__ import annotations

import pytest
from src.backtest import costs
from src.storage.models import Side


def test_fee_abs():
    assert costs.fee_abs(10_000.0, 5.0) == pytest.approx(5.0)  # 5 bps of 10k


def test_apply_slippage_directions():
    # LONG entry = buy -> price up; LONG exit = sell -> price down
    assert costs.apply_slippage(100.0, Side.LONG, 10.0, is_entry=True) == pytest.approx(100.1)
    assert costs.apply_slippage(100.0, Side.LONG, 10.0, is_entry=False) == pytest.approx(99.9)
    # SHORT entry = sell -> down; SHORT exit = buy -> up
    assert costs.apply_slippage(100.0, Side.SHORT, 10.0, is_entry=True) == pytest.approx(99.9)
    assert costs.apply_slippage(100.0, Side.SHORT, 10.0, is_entry=False) == pytest.approx(100.1)


def test_slippage_models():
    assert costs.slippage_bps("none") == 0.0
    assert costs.slippage_bps("fixed", fixed_bps=2.0) == 2.0
    assert costs.slippage_bps("vol", vol_coef=0.5, atr_pct=0.02) == pytest.approx(0.01)
    with pytest.raises(NotImplementedError):
        costs.slippage_bps("spread")


def test_atr_uses_true_range():
    candles = [
        {"high": 101, "low": 99, "close": 100},
        {"high": 103, "low": 100, "close": 102},
        {"high": 104, "low": 101, "close": 103},
    ]
    a = costs.atr(candles, period=14)
    assert len(a) == 3
    assert a[0] == pytest.approx(2.0)            # first TR = high-low
    assert a[1] == pytest.approx((2.0 + 3.0) / 2)  # TR2 = max(3, |103-100|, |100-100|)=3


def test_funding_sign_by_side():
    rows = [{"timestamp": 100, "funding_rate": 0.001}]
    # rate>0: LONG pays (positive cost), SHORT receives (negative cost)
    long_cost = costs.funding_cost(10_000.0, Side.LONG, rows, entry_ts=0, exit_ts=200)
    short_cost = costs.funding_cost(10_000.0, Side.SHORT, rows, entry_ts=0, exit_ts=200)
    assert long_cost == pytest.approx(10.0)
    assert short_cost == pytest.approx(-10.0)


def test_funding_window_excludes_outside():
    rows = [{"timestamp": 50, "funding_rate": 0.001}, {"timestamp": 500, "funding_rate": 0.001}]
    # only the event in (entry, exit] counts
    cost = costs.funding_cost(10_000.0, Side.LONG, rows, entry_ts=100, exit_ts=300)
    assert cost == pytest.approx(0.0)  # 50 is before entry, 500 is after exit


def test_funding_none_rows():
    assert costs.funding_cost(10_000.0, Side.LONG, None, 0, 100) == 0.0
