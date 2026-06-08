"""Trade-summary metrics over a DataFrame of TradeRecord rows.

Single source for both the live baseline report (``research/baseline_report.py``) and
the backtest report (``src/backtest/report.py``), so they cannot drift.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def _f(value: Any) -> float:
    """NA/None-safe float (empty or all-NA aggregates -> 0.0)."""
    if value is None or pd.isna(value):
        return 0.0
    return float(value)


def max_drawdown(pnl_sorted: pd.Series) -> float:
    """Max drawdown of the cumulative pnl. Equity starts at 0 (the initial peak) so a
    losing first trade counts as drawdown. Returns a value <= 0."""
    if pnl_sorted.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), pnl_sorted.reset_index(drop=True)]).cumsum()
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def breakdown(trades: pd.DataFrame, key: str) -> list[dict[str, Any]]:
    if trades.empty or key not in trades:
        return []
    out = []
    for val, grp in trades.groupby(key, dropna=False):
        pnl = grp["pnl_abs"]
        out.append(
            {
                key: None if pd.isna(val) else val,
                "trade_count": int(len(grp)),
                "win_rate": _f((pnl > 0).mean()) if len(grp) else 0.0,
                "net_pnl": _f(pnl.sum()),
                "avg_r": _f(grp["pnl_r"].mean()) if "pnl_r" in grp else 0.0,
            }
        )
    return out


def trade_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    """Compute the standard trade metrics. Empty input yields ``{"trade_count": 0}``."""
    t: dict[str, Any] = {"trade_count": int(len(trades))}
    if trades.empty:
        return t

    pnl = trades["pnl_abs"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    t["win_rate"] = _f((pnl > 0).mean())
    t["avg_win"] = _f(wins.mean()) if len(wins) else 0.0
    t["avg_loss"] = _f(losses.mean()) if len(losses) else 0.0
    gross_loss = abs(_f(losses.sum()))
    t["profit_factor"] = _f(wins.sum()) / gross_loss if gross_loss else float("inf")
    t["net_pnl"] = _f(pnl.sum())
    t["average_r"] = _f(trades["pnl_r"].mean()) if "pnl_r" in trades else 0.0
    t["median_r"] = _f(trades["pnl_r"].median()) if "pnl_r" in trades else 0.0
    if "exit_time" in trades:
        ordered = trades.sort_values("exit_time")["pnl_abs"]
        t["max_drawdown"] = max_drawdown(ordered)
    t["avg_mfe_r"] = _f(trades["mfe_r"].mean()) if "mfe_r" in trades else 0.0
    t["avg_mae_r"] = _f(trades["mae_r"].mean()) if "mae_r" in trades else 0.0

    work = trades
    if "entry_time" in trades:
        hours = pd.to_datetime(trades["entry_time"], utc=True, errors="coerce").dt.hour
        work = trades.assign(_hour=hours)
    t["by_regime"] = breakdown(work, "regime")
    t["by_setup"] = breakdown(work, "setup_name")
    t["by_hour"] = breakdown(work, "_hour")
    t["by_side"] = breakdown(work, "side")
    return t
