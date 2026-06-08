"""Cost models — pure functions (fee / slippage / funding).

Sign conventions (fixed by tests, since changing them later moves every backtest result):

- ``fee_abs`` and the funding *cost* are returned as **positive = a loss to the trade**.
  The engine subtracts them from pnl.
- Slippage moves the fill price **against** the trader: a worse entry and a worse exit.
- Funding: with a positive funding rate, a LONG **pays** (positive cost) and a SHORT
  **receives** (negative cost). Sign flips with the rate.
"""

from __future__ import annotations

from typing import Any

from ..storage.models import Side


def fee_abs(notional: float, bps: float) -> float:
    """Fee for a fill of ``notional`` notional at ``bps`` basis points (positive cost)."""
    return abs(notional) * bps / 10_000.0


def true_range(high: float, low: float, prev_close: float | None) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(candles: list[dict[str, Any]], period: int = 14) -> list[float]:
    """Wilder-style ATR per candle (simple rolling mean of true range).

    Returns one value per candle; the first ``period-1`` use the available window.
    """
    trs: list[float] = []
    prev_close: float | None = None
    out: list[float] = []
    for c in candles:
        tr = true_range(c["high"], c["low"], prev_close)
        trs.append(tr)
        window = trs[-period:]
        out.append(sum(window) / len(window))
        prev_close = c["close"]
    return out


def slippage_bps(
    model: str,
    *,
    fixed_bps: float = 0.0,
    vol_coef: float = 0.0,
    atr_pct: float = 0.0,
) -> float:
    """Slippage in bps for the given model.

    - ``none``  -> 0
    - ``fixed`` -> ``fixed_bps``
    - ``vol``   -> ``vol_coef * atr_pct`` (atr_pct = ATR / price)
    - ``spread``-> NotImplementedError (spread/orderbook data not collected yet; TODO PR#5+)
    """
    if model == "none":
        return 0.0
    if model == "fixed":
        return fixed_bps
    if model == "vol":
        return vol_coef * atr_pct
    if model == "spread":
        raise NotImplementedError(
            "spread-based slippage requires orderbook/spread data not yet collected"
        )
    raise ValueError(f"unknown slippage model: {model}")


def apply_slippage(price: float, side: Side, bps: float, *, is_entry: bool) -> float:
    """Move ``price`` against the trader by ``bps``.

    LONG entry / SHORT exit -> buy -> price goes UP.
    LONG exit / SHORT entry -> sell -> price goes DOWN.
    """
    buying = (side == Side.LONG and is_entry) or (side == Side.SHORT and not is_entry)
    factor = 1.0 + bps / 10_000.0 if buying else 1.0 - bps / 10_000.0
    return price * factor


def funding_cost(
    notional: float,
    side: Side,
    funding_rows: list[dict[str, Any]] | None,
    entry_ts: int,
    exit_ts: int,
) -> float:
    """Sum funding over the holding window. Positive return = cost (loss).

    A funding row is ``{"timestamp": ms, "funding_rate": r}``. For each funding event
    inside ``(entry_ts, exit_ts]``, a LONG pays ``r * notional`` (positive cost when
    ``r>0``) and a SHORT receives it (negative cost). Returns 0 if no rows.
    """
    if not funding_rows:
        return 0.0
    sign = 1.0 if side == Side.LONG else -1.0
    total = 0.0
    for row in funding_rows:
        ts = int(row["timestamp"])
        if entry_ts < ts <= exit_ts:
            rate = row.get("funding_rate")
            if rate is None:
                continue
            total += sign * float(rate) * abs(notional)
    return total
