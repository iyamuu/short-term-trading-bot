from __future__ import annotations

import sys
from pathlib import Path

from conftest import make_candidate, make_trade

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research import baseline_report  # noqa: E402
from src.storage import candidate_log, trade_log  # noqa: E402
from src.storage.models import ExitReason, Regime, Side  # noqa: E402


def test_max_drawdown_counts_losing_first_trade():
    import pandas as pd
    from src.analytics.metrics import max_drawdown

    # equity starts at 0: a -1 first trade is a -1 drawdown, recovered to -0.5
    assert max_drawdown(pd.Series([-1.0, 0.5])) == -1.0
    assert max_drawdown(pd.Series([2.0, -1.0])) == -1.0
    assert max_drawdown(pd.Series([1.0, 1.0])) == 0.0


def test_empty_is_safe(base_dir):
    report = baseline_report.compute_baseline(base_dir)
    assert report["trades"]["trade_count"] == 0
    assert report["candidates"]["candidate_count"] == 0
    md = baseline_report.render_markdown(report)
    assert "No trades recorded yet" in md


def test_metrics_match_known_trades(base_dir):
    trades = [
        make_trade(trade_id="w1", pnl_abs=2.0, pnl_r=2.0, exit_reason=ExitReason.HIT_TP2,
                   regime=Regime.TREND_UP),
        make_trade(trade_id="w2", pnl_abs=2.0, pnl_r=2.0, exit_reason=ExitReason.HIT_TP2,
                   regime=Regime.TREND_UP),
        make_trade(trade_id="l1", pnl_abs=-1.0, pnl_r=-1.0, exit_reason=ExitReason.HIT_SL,
                   regime=Regime.RANGE, side=Side.SHORT),
    ]
    trade_log.append(base_dir, trades)
    candidate_log.append(base_dir, [
        make_candidate(candidate_id="c1", setup_name="breakout"),
        make_candidate(candidate_id="c2", setup_name="breakout"),
    ])

    report = baseline_report.compute_baseline(base_dir)
    t = report["trades"]
    assert t["trade_count"] == 3
    assert t["win_rate"] == 2 / 3
    # profit factor = 4 / 1 = 4
    assert t["profit_factor"] == 4.0
    assert t["average_r"] == 1.0   # (2+2-1)/3
    assert t["net_pnl"] == 3.0

    c = report["candidates"]
    assert c["candidate_count"] == 2
    assert c["by_reject_reason"]["REGIME_NG"] == 2
    # breakout: 0 accepted, 2 rejected -> rejection rate 1.0
    assert c["rejection_rate_by_setup"]["breakout"] == 1.0


def test_render_markdown_with_data(base_dir):
    trade_log.append(base_dir, [make_trade()])
    report = baseline_report.compute_baseline(base_dir)
    md = baseline_report.render_markdown(report)
    assert "trade_count: 1" in md
