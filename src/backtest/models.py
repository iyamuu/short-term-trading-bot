"""Backtest input/config/result dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..storage.models import Side, TradeRecord


@dataclass
class EntryIntent:
    """A precomputed entry signal injected into the engine (strategy-agnostic).

    R is defined by ``abs(entry_price - sl)``. ``tp2`` may be None (TP1-only plan).
    """

    timestamp: int  # candle open time (epoch ms) at which the entry is placed
    side: Side
    entry_price: float
    sl: float
    tp1: float
    tp2: float | None = None
    size: float = 1.0
    signal_id: str | None = None
    regime: str | None = None
    setup_name: str | None = None
    confidence: float | None = None
    entry_reason: str | None = None

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.sl)


@dataclass
class BacktestCostConfig:
    """Simulation-only cost parameters (kept separate from StrategyConfig)."""

    fee_maker_bps: float = 2.0
    fee_taker_bps: float = 5.0
    slippage_model: str = "fixed"  # "none" | "fixed" | "vol" | "spread"(not impl)
    slippage_fixed_bps: float = 1.0
    slippage_vol_coef: float = 0.1  # bps per 1.0 atr_pct (i.e. per 100% ATR)
    funding_enabled: bool = True
    tp1_close_fraction: float = 0.5  # fraction of size closed at TP1

    def zeroed(self) -> BacktestCostConfig:
        """A copy with all costs disabled (for costs-off comparison)."""
        return BacktestCostConfig(
            fee_maker_bps=0.0,
            fee_taker_bps=0.0,
            slippage_model="none",
            slippage_fixed_bps=0.0,
            slippage_vol_coef=0.0,
            funding_enabled=False,
            tp1_close_fraction=self.tp1_close_fraction,
        )


@dataclass
class SkippedIntent:
    timestamp: int
    signal_id: str | None
    reason: str  # POSITION_OPEN | DUPLICATE_TIMESTAMP


@dataclass
class BacktestResult:
    trades: list[TradeRecord] = field(default_factory=list)
    skipped: list[SkippedIntent] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    cost_impact: dict[str, Any] = field(default_factory=dict)
