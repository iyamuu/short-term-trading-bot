"""Order-argument construction for CCXT (Phase 0) — pure, no network.

IMPORTANT framing (per review): these functions build **CCXT-unified** arguments for
``exchange.create_order(symbol, type, side, amount, price, params)`` — NOT raw Bitget
REST request bodies. The raw Bitget mapping (``marginCoin``, ``reduceOnly`` as YES/NO,
``triggerPrice``/``executePrice``/``planType``/``holdSide`` for TPSL/trigger orders) is
CCXT's responsibility; pinning the exact REST call is deferred to the execution phase
and will be done against a **mocked CCXT client**, not asserted here.

What we *do* pin now is our own invariants, expressed in CCXT-unified terms:

- one-way mode (position mode is set once via ``setPositionMode(hedged=False)``)
- closing orders MUST carry ``reduceOnly: True`` (CCXT-unified boolean)
- stop-loss = market trigger order via ``triggerPrice`` + ``triggerType: mark_price``
  (``triggerType`` is a Bitget passthrough in ``params``)
- every order carries a ``clientOid`` for idempotency
"""

from __future__ import annotations

from typing import Any

from ..storage.models import OrderRole, Side

# Bitget passthrough: trigger evaluated against the mark price (set in CCXT params).
TRIGGER_TYPE_MARK = "mark_price"
DEFAULT_MARGIN_MODE = "isolated"


def _close_side(position_side: Side) -> str:
    """Reducing a LONG sells; reducing a SHORT buys."""
    return "sell" if position_side == Side.LONG else "buy"


def _open_side(position_side: Side) -> str:
    return "buy" if position_side == Side.LONG else "sell"


def build_entry_order(
    *,
    symbol: str,
    side: Side,
    size: float,
    client_oid: str,
    price: float | None = None,
) -> dict[str, Any]:
    """Entry order (market if ``price`` is None, else limit). Never reduceOnly."""
    return {
        "symbol": symbol,
        "type": "limit" if price is not None else "market",
        "side": _open_side(side),
        "amount": size,
        "price": price,
        "params": {
            "reduceOnly": False,
            "marginMode": DEFAULT_MARGIN_MODE,
            "clientOid": client_oid,
        },
    }


def build_reduce_order(
    *,
    symbol: str,
    position_side: Side,
    size: float,
    client_oid: str,
    role: OrderRole,
    price: float | None = None,
) -> dict[str, Any]:
    """A TP (or any partial/full close) order. Always reduceOnly."""
    if role not in (OrderRole.TP1, OrderRole.TP2):
        raise ValueError(f"build_reduce_order is for TP roles, got {role}")
    return {
        "symbol": symbol,
        "type": "limit" if price is not None else "market",
        "side": _close_side(position_side),
        "amount": size,
        "price": price,
        "params": {
            "reduceOnly": True,
            "marginMode": DEFAULT_MARGIN_MODE,
            "clientOid": client_oid,
            "orderRole": role.value,
        },
    }


def build_stop_loss_order(
    *,
    symbol: str,
    position_side: Side,
    size: float,
    stop_price: float,
    client_oid: str,
) -> dict[str, Any]:
    """Stop-loss: market trigger order on mark price, reduceOnly."""
    return {
        "symbol": symbol,
        "type": "market",
        "side": _close_side(position_side),
        "amount": size,
        "price": None,
        "params": {
            "reduceOnly": True,
            "marginMode": DEFAULT_MARGIN_MODE,
            "triggerPrice": stop_price,
            "triggerType": TRIGGER_TYPE_MARK,
            "clientOid": client_oid,
            "orderRole": OrderRole.SL.value,
        },
    }
