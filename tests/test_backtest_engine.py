from __future__ import annotations

import pytest
from src.backtest.engine import run_backtest
from src.backtest.models import BacktestCostConfig, EntryIntent
from src.backtest.report import compare_costs
from src.config import StrategyConfig
from src.storage.models import ExitReason, Side

TF = 60_000


def cand(i, o, h, low, c, v=1.0):
    return {"timestamp": i * TF, "open": o, "high": h, "low": low, "close": c, "volume": v}


def no_cost(tp1_fraction=0.5):
    return BacktestCostConfig(
        fee_maker_bps=0.0, fee_taker_bps=0.0, slippage_model="none",
        slippage_fixed_bps=0.0, slippage_vol_coef=0.0, funding_enabled=False,
        tp1_close_fraction=tp1_fraction,
    )


def run(candles, intents, cost=None, strat=None):
    return run_backtest(candles, intents, strat or StrategyConfig(), cost or no_cost())


def test_long_tp1_breakeven_tp2():
    candles = [
        cand(0, 100, 100.5, 99.5, 100.2),   # entry bar, no exit
        cand(1, 100.2, 101.0, 100.0, 100.8),  # TP1 -> breakeven SL=100
        cand(2, 100.8, 102.0, 100.5, 101.5),  # TP2 (SL 100 not touched)
    ]
    intent = EntryIntent(timestamp=0, side=Side.LONG, entry_price=100, sl=99, tp1=101, tp2=102)
    res = run(candles, [intent])
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.hit_tp1 and t.hit_tp2 and t.moved_to_breakeven
    assert t.exit_reason == ExitReason.HIT_TP2
    assert t.pnl_r == pytest.approx(1.5)   # 1R*0.5 + 2R*0.5
    assert t.pnl_abs == pytest.approx(1.5)


def test_long_stop_loss():
    candles = [
        cand(0, 100, 100.2, 99.5, 100.0),
        cand(1, 100.0, 100.0, 98.5, 99.0),   # SL hit
    ]
    intent = EntryIntent(timestamp=0, side=Side.LONG, entry_price=100, sl=99, tp1=101, tp2=102)
    t = run(candles, [intent]).trades[0]
    assert t.exit_reason == ExitReason.HIT_SL
    assert not t.hit_tp1
    assert t.pnl_r == pytest.approx(-1.0)


def test_short_tp1_tp2():
    candles = [
        cand(0, 100, 100.5, 99.5, 99.8),
        cand(1, 99.8, 100.0, 99.0, 99.2),    # TP1=99 -> breakeven 100
        cand(2, 99.2, 99.5, 98.0, 98.5),     # TP2=98
    ]
    intent = EntryIntent(timestamp=0, side=Side.SHORT, entry_price=100, sl=101, tp1=99, tp2=98)
    t = run(candles, [intent]).trades[0]
    assert t.hit_tp1 and t.hit_tp2
    assert t.exit_reason == ExitReason.HIT_TP2
    assert t.pnl_r == pytest.approx(1.5)


def test_intra_candle_collision_sl_wins():
    # entry bar touches BOTH sl(99) and tp1(101) -> conservative SL
    candles = [cand(0, 100, 101.5, 98.5, 100.0)]
    intent = EntryIntent(timestamp=0, side=Side.LONG, entry_price=100, sl=99, tp1=101, tp2=102)
    t = run(candles, [intent]).trades[0]
    assert t.exit_reason == ExitReason.HIT_SL
    assert not t.hit_tp1
    assert t.pnl_r == pytest.approx(-1.0)


def test_partial_fraction():
    candles = [
        cand(0, 100, 100.5, 99.5, 100.2),
        cand(1, 100.2, 101.0, 100.0, 100.8),
        cand(2, 100.8, 102.0, 100.5, 101.5),
    ]
    intent = EntryIntent(timestamp=0, side=Side.LONG, entry_price=100, sl=99, tp1=101, tp2=102)
    t = run(candles, [intent], cost=no_cost(tp1_fraction=0.3)).trades[0]
    # 1R*0.3 + 2R*0.7 = 1.7
    assert t.pnl_r == pytest.approx(1.7)


def test_trailing_after_2r():
    candles = [
        cand(0, 100, 100.5, 99.5, 100.2),
        cand(1, 100.2, 101.0, 100.0, 100.8),   # TP1 -> breakeven 100
        cand(2, 100.8, 103.0, 100.5, 102.5),   # 3R favorable -> trail SL to 102
        cand(3, 102.5, 104.0, 102.0, 103.0),   # SL(102) hit -> TRAILING_SL
    ]
    intent = EntryIntent(timestamp=0, side=Side.LONG, entry_price=100, sl=99, tp1=101, tp2=None)
    t = run(candles, [intent]).trades[0]
    assert t.hit_tp1
    assert t.exit_reason == ExitReason.TRAILING_SL
    assert t.pnl_r == pytest.approx(1.5)   # 1R*0.5 + 2R*0.5 (closed at 102)


def test_skipped_intents_one_way_and_duplicate():
    candles = [
        cand(0, 100, 100.5, 99.5, 100.2),
        cand(1, 100.2, 101.0, 100.0, 100.5),   # position still open (tp far)
        cand(2, 100.5, 100.8, 100.0, 100.4),
    ]
    a = EntryIntent(timestamp=0, side=Side.LONG, entry_price=100, sl=99, tp1=110, tp2=120,
                    signal_id="A")
    b = EntryIntent(timestamp=0, side=Side.LONG, entry_price=100, sl=99, tp1=110, tp2=120,
                    signal_id="B")  # duplicate timestamp
    c = EntryIntent(timestamp=1 * TF, side=Side.LONG, entry_price=100, sl=99, tp1=110, tp2=120,
                    signal_id="C")  # arrives while A open
    res = run(candles, [a, b, c])
    reasons = {s.signal_id: s.reason for s in res.skipped}
    assert reasons["B"] == "DUPLICATE_TIMESTAMP"
    assert reasons["C"] == "POSITION_OPEN"


def test_compare_costs_reports_drag():
    candles = [
        cand(0, 100, 100.5, 99.5, 100.2),
        cand(1, 100.2, 101.0, 100.0, 100.8),
        cand(2, 100.8, 102.0, 100.5, 101.5),
    ]
    intent = EntryIntent(timestamp=0, side=Side.LONG, entry_price=100, sl=99, tp1=101, tp2=102)
    cost = BacktestCostConfig(fee_taker_bps=5.0, slippage_model="fixed", slippage_fixed_bps=2.0,
                              funding_enabled=False)
    diff = compare_costs(candles, [intent], StrategyConfig(), cost)
    assert diff["net_pnl_costs_off"] > diff["net_pnl_costs_on"]
    assert diff["cost_drag"] > 0
    assert diff["fee_total"] > 0
    assert diff["slippage_total"] > 0
