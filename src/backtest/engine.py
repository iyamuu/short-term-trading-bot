"""Event-driven backtest engine.

Fixed, conservative conventions (pinned by tests — changing them moves every result):

- Entry fills at the **open** of the intent's candle; that same candle's high/low are
  eligible for exit.
- Intra-candle evaluation order is **SL -> TP1 -> TP2 -> trailing**. At most one action
  per candle: once TP1 fills, breakeven-SL / TP2 / trailing are not also judged in the
  same candle (they apply from the next candle).
- One-way: a single open position. Intents arriving while open, or duplicate-timestamp
  intents, are recorded as skipped (not silently dropped).
- ``pnl_r`` is the **net** (post fee/slippage/funding) R, size-weighted across partials.
- All fills use the taker fee (conservative). Slippage moves fills against the trader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config import BOT_VERSION, FEATURES_SCHEMA_VERSION, STRATEGY_VERSION, StrategyConfig
from ..storage.models import ExcursionTracker, ExitReason, Side, TradeRecord
from . import costs
from .models import (
    BacktestCostConfig,
    BacktestResult,
    EntryIntent,
    SkippedIntent,
)
from .report import build_result


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


@dataclass
class _Close:
    price: float       # fill price (slippage applied)
    size: float
    reason: ExitReason


@dataclass
class _OpenPosition:
    intent: EntryIntent
    entry_ref: float          # candle open used as entry reference
    entry_fill: float         # entry_ref after slippage
    entry_ts: int
    risk_per_unit: float
    current_sl: float
    remaining: float
    tracker: ExcursionTracker
    fees: float = 0.0
    slippage_est: float = 0.0
    closes: list[_Close] = field(default_factory=list)
    hit_tp1: bool = False
    hit_tp2: bool = False
    moved_to_breakeven: bool = False
    trailing_active: bool = False
    exit_ts: int | None = None


def _atr_pct(candle: dict[str, Any]) -> float:
    atr = candle.get("_atr", 0.0)
    close = candle.get("close", 0.0)
    return (atr / close) if close else 0.0


def _slip_bps(cost: BacktestCostConfig, candle: dict[str, Any]) -> float:
    return costs.slippage_bps(
        cost.slippage_model,
        fixed_bps=cost.slippage_fixed_bps,
        vol_coef=cost.slippage_vol_coef,
        atr_pct=_atr_pct(candle),
    )


def _fill(
    pos: _OpenPosition, ref_price: float, size: float, candle: dict[str, Any],
    cost: BacktestCostConfig, *, is_entry: bool,
) -> float:
    """Apply slippage + accrue fee/slippage estimate; return the fill price."""
    bps = _slip_bps(cost, candle)
    fill = costs.apply_slippage(ref_price, pos.intent.side, bps, is_entry=is_entry)
    pos.slippage_est += abs(fill - ref_price) * size
    pos.fees += costs.fee_abs(fill * size, cost.fee_taker_bps)
    return fill


def _open(intent: EntryIntent, candle: dict[str, Any], cost: BacktestCostConfig) -> _OpenPosition:
    entry_ref = candle["open"]
    risk = abs(entry_ref - intent.sl)
    pos = _OpenPosition(
        intent=intent,
        entry_ref=entry_ref,
        entry_fill=entry_ref,  # set below
        entry_ts=int(candle["timestamp"]),
        risk_per_unit=risk,
        current_sl=intent.sl,
        remaining=intent.size,
        tracker=ExcursionTracker(side=intent.side, entry_price=entry_ref, initial_sl=intent.sl),
    )
    pos.entry_fill = _fill(pos, entry_ref, intent.size, candle, cost, is_entry=True)
    return pos


def _sl_touched(pos: _OpenPosition, candle: dict[str, Any]) -> bool:
    if pos.intent.side == Side.LONG:
        return candle["low"] <= pos.current_sl
    return candle["high"] >= pos.current_sl


def _level_touched(pos: _OpenPosition, candle: dict[str, Any], level: float) -> bool:
    if pos.intent.side == Side.LONG:
        return candle["high"] >= level
    return candle["low"] <= level


def _sl_reason(pos: _OpenPosition) -> ExitReason:
    if pos.trailing_active:
        return ExitReason.TRAILING_SL
    if pos.moved_to_breakeven:
        return ExitReason.BREAKEVEN_SL
    return ExitReason.HIT_SL


def _close_remaining(
    pos: _OpenPosition, level: float, candle: dict[str, Any],
    cost: BacktestCostConfig, reason: ExitReason,
) -> None:
    fill = _fill(pos, level, pos.remaining, candle, cost, is_entry=False)
    pos.closes.append(_Close(price=fill, size=pos.remaining, reason=reason))
    pos.remaining = 0.0
    pos.exit_ts = int(candle["timestamp"])


def _partial_close(
    pos: _OpenPosition, level: float, size: float, candle: dict[str, Any],
    cost: BacktestCostConfig, reason: ExitReason,
) -> None:
    size = min(size, pos.remaining)
    fill = _fill(pos, level, size, candle, cost, is_entry=False)
    pos.closes.append(_Close(price=fill, size=size, reason=reason))
    pos.remaining -= size


def _maybe_trail(pos: _OpenPosition, candle: dict[str, Any], trailing_after_r: float) -> None:
    if pos.risk_per_unit <= 0:
        return
    if pos.intent.side == Side.LONG:
        fav_r = (candle["high"] - pos.entry_ref) / pos.risk_per_unit
        if fav_r >= trailing_after_r:
            new_sl = pos.entry_ref + (fav_r - 1.0) * pos.risk_per_unit
            if new_sl > pos.current_sl:
                pos.current_sl = new_sl
                pos.trailing_active = True
    else:
        fav_r = (pos.entry_ref - candle["low"]) / pos.risk_per_unit
        if fav_r >= trailing_after_r:
            new_sl = pos.entry_ref - (fav_r - 1.0) * pos.risk_per_unit
            if new_sl < pos.current_sl:
                pos.current_sl = new_sl
                pos.trailing_active = True


def _process_candle(
    pos: _OpenPosition, candle: dict[str, Any],
    strat: StrategyConfig, cost: BacktestCostConfig,
) -> bool:
    """Evaluate one candle against the open position. Returns True if fully closed.

    Order is fixed: SL -> TP1 -> TP2 -> trailing, at most one action per candle.
    """
    # 1. SL (highest priority — conservative on intra-candle collision)
    if _sl_touched(pos, candle):
        _close_remaining(pos, pos.current_sl, candle, cost, _sl_reason(pos))
        return True

    # 2. TP1 (partial). After filling, do NOT also judge breakeven/TP2/trailing this candle.
    if not pos.hit_tp1:
        if _level_touched(pos, candle, pos.intent.tp1):
            close_size = pos.intent.size * cost.tp1_close_fraction
            _partial_close(pos, pos.intent.tp1, close_size, candle, cost, ExitReason.HIT_TP1)
            pos.hit_tp1 = True
            if strat.move_sl_to_breakeven_after_tp1:
                pos.current_sl = pos.entry_ref
                pos.moved_to_breakeven = True
            return pos.remaining <= 0
        return False

    # 3. TP2 (close remaining)
    if pos.intent.tp2 is not None and not pos.hit_tp2:
        if _level_touched(pos, candle, pos.intent.tp2):
            _close_remaining(pos, pos.intent.tp2, candle, cost, ExitReason.HIT_TP2)
            pos.hit_tp2 = True
            return True

    # 4. trailing update (no close)
    _maybe_trail(pos, candle, strat.trailing_after_r)
    return False


def _finalize(
    pos: _OpenPosition, strat: StrategyConfig, cost: BacktestCostConfig,
    funding_rows: list[dict[str, Any]] | None,
) -> TradeRecord:
    side = pos.intent.side
    size = pos.intent.size
    exit_ts = pos.exit_ts if pos.exit_ts is not None else pos.entry_ts

    gross = 0.0
    for c in pos.closes:
        unit = (c.price - pos.entry_fill) if side == Side.LONG else (pos.entry_fill - c.price)
        gross += unit * c.size

    notional = pos.entry_fill * size
    funding = (
        costs.funding_cost(notional, side, funding_rows, pos.entry_ts, exit_ts)
        if cost.funding_enabled else 0.0
    )
    net = gross - pos.fees - funding

    risk_amount = pos.risk_per_unit * size
    pnl_r = net / risk_amount if risk_amount else 0.0
    pnl_pct = net / notional if notional else 0.0

    last = pos.closes[-1] if pos.closes else None
    pos.tracker.update(pos.entry_ref)  # ensure at least one sample
    rec = TradeRecord(
        trade_id=pos.intent.signal_id or f"bt-{pos.entry_ts}",
        signal_id=pos.intent.signal_id or f"sig-{pos.entry_ts}",
        side=side,
        entry_time=_iso(pos.entry_ts),
        entry_price=pos.entry_fill,
        entry_size=size,
        exit_time=_iso(exit_ts),
        exit_price=last.price if last else None,
        exit_reason=last.reason if last else ExitReason.MANUAL,
        pnl_abs=net,
        pnl_pct=pnl_pct,
        pnl_r=pnl_r,
        fee_abs=pos.fees,
        funding_abs=funding,
        slippage_estimated=pos.slippage_est,
        initial_sl=pos.intent.sl,
        initial_tp1=pos.intent.tp1,
        initial_tp2=pos.intent.tp2,
        final_sl=pos.current_sl,
        hit_tp1=pos.hit_tp1,
        hit_tp2=pos.hit_tp2,
        moved_to_breakeven=pos.moved_to_breakeven,
        holding_minutes=(exit_ts - pos.entry_ts) / 60_000.0,
        regime=None,
        setup_name=pos.intent.setup_name,
        entry_reason=pos.intent.entry_reason,
        confidence=pos.intent.confidence,
        risk_size_multiplier=1.0,
        features_schema_version=FEATURES_SCHEMA_VERSION,
        config_hash=strat.config_hash(),
        strategy_params_snapshot=strat.model_dump(),
        bot_version=BOT_VERSION,
        strategy_version=STRATEGY_VERSION,
        model_version=None,
    )
    # regime is a free string on the intent; TradeRecord.regime is an enum, leave None.
    rec.apply_excursion(pos.tracker)
    return rec


def run_backtest(
    candles: list[dict[str, Any]],
    intents: list[EntryIntent],
    strategy_config: StrategyConfig | None = None,
    cost_config: BacktestCostConfig | None = None,
    funding_rows: list[dict[str, Any]] | None = None,
) -> BacktestResult:
    strat = strategy_config or StrategyConfig()
    cost = cost_config or BacktestCostConfig()

    # precompute ATR for vol-based slippage
    atr_list = costs.atr(candles)
    for c, a in zip(candles, atr_list, strict=True):
        c["_atr"] = a

    by_ts: dict[int, list[EntryIntent]] = {}
    for it in intents:
        by_ts.setdefault(int(it.timestamp), []).append(it)

    result = BacktestResult()
    pos: _OpenPosition | None = None

    for candle in candles:
        ts = int(candle["timestamp"])

        # 1. progress an existing position on this candle
        if pos is not None:
            pos.tracker.update(candle["high"])
            pos.tracker.update(candle["low"])
            if _process_candle(pos, candle, strat, cost):
                result.trades.append(_finalize(pos, strat, cost, funding_rows))
                pos = None

        # 2. entries at this timestamp
        group = by_ts.get(ts)
        if group:
            if pos is not None:
                for it in group:
                    result.skipped.append(SkippedIntent(ts, it.signal_id, "POSITION_OPEN"))
            else:
                chosen = group[0]
                for extra in group[1:]:
                    result.skipped.append(
                        SkippedIntent(ts, extra.signal_id, "DUPLICATE_TIMESTAMP")
                    )
                pos = _open(chosen, candle, cost)
                # entry candle is eligible for exit
                pos.tracker.update(candle["high"])
                pos.tracker.update(candle["low"])
                if _process_candle(pos, candle, strat, cost):
                    result.trades.append(_finalize(pos, strat, cost, funding_rows))
                    pos = None

    return build_result(result, strat, cost, candles, intents, funding_rows)
