"""Assemble backtest metrics + cost impact from a populated BacktestResult."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..analytics.metrics import trade_metrics
from .models import BacktestCostConfig, BacktestResult, EntryIntent


def _cost_totals(result: BacktestResult) -> dict[str, float]:
    fee = sum((t.fee_abs or 0.0) for t in result.trades)
    slip = sum((t.slippage_estimated or 0.0) for t in result.trades)
    funding = sum((t.funding_abs or 0.0) for t in result.trades)
    net = sum((t.pnl_abs or 0.0) for t in result.trades)
    return {
        "fee_total": fee,
        "slippage_total": slip,
        "funding_total": funding,
        "net_pnl": net,
    }


def build_result(
    result: BacktestResult,
    strategy_config: Any,
    cost_config: BacktestCostConfig,
    candles: list[dict[str, Any]],
    intents: list[EntryIntent],
    funding_rows: list[dict[str, Any]] | None,
) -> BacktestResult:
    """Fill ``metrics`` and ``cost_impact`` (totals only; no re-run)."""
    df = pd.DataFrame([t.to_row() for t in result.trades]) if result.trades else pd.DataFrame()
    result.metrics = trade_metrics(df)
    result.cost_impact = _cost_totals(result)
    return result


def compare_costs(
    candles: list[dict[str, Any]],
    intents: list[EntryIntent],
    strategy_config: Any = None,
    cost_config: BacktestCostConfig | None = None,
    funding_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run with costs ON and OFF; report the net-pnl difference and cost totals.

    Satisfies the Issue Phase 2 criterion: the difference vs a fee/slippage/funding=0 run
    is reported. Imported lazily to avoid an engine<->report import cycle.
    """
    from .engine import run_backtest  # noqa: PLC0415

    cost = cost_config or BacktestCostConfig()
    on = run_backtest(candles, intents, strategy_config, cost, funding_rows)
    off = run_backtest(candles, intents, strategy_config, cost.zeroed(), funding_rows)

    net_on = on.cost_impact["net_pnl"]
    net_off = off.cost_impact["net_pnl"]
    return {
        "net_pnl_costs_on": net_on,
        "net_pnl_costs_off": net_off,
        "cost_drag": net_off - net_on,  # >= 0: how much costs ate
        "fee_total": on.cost_impact["fee_total"],
        "slippage_total": on.cost_impact["slippage_total"],
        "funding_total": on.cost_impact["funding_total"],
    }
