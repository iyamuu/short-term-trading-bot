"""Bitget order-parameter construction (Phase 0) — pure, no network.

This module ONLY builds the parameter dicts that will later be handed to CCXT /
Bitget REST. Keeping construction pure lets us pin Bitget's quirks in tests before any
live wiring exists (Issue Phase 0: "fix one-way / hedged:False / reduceOnly / stop
order spec in tests"):

- one-way mode, ``hedged: False``
- closing orders MUST be ``reduceOnly``
- stop-loss = market order + ``stopPrice`` + ``triggerType: mark_price``
- every order carries a ``clientOid`` for idempotency

Actual submission (CCXT client, retries, reconciliation) lands in later phases.
"""

from __future__ import annotations

from typing import Any

from ..storage.models import OrderRole, Side

PRODUCT_TYPE = "USDT-FUTURES"
TRIGGER_TYPE_MARK = "mark_price"


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
    order_type = "limit" if price is not None else "market"
    params: dict[str, Any] = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "side": _open_side(side),
        "orderType": order_type,
        "size": size,
        "reduceOnly": False,
        "hedged": False,
        "clientOid": client_oid,
    }
    if price is not None:
        params["price"] = price
    return params


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
    order_type = "limit" if price is not None else "market"
    params: dict[str, Any] = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "side": _close_side(position_side),
        "orderType": order_type,
        "size": size,
        "reduceOnly": True,
        "hedged": False,
        "clientOid": client_oid,
        "orderRole": role.value,
    }
    if price is not None:
        params["price"] = price
    return params


def build_stop_loss_order(
    *,
    symbol: str,
    position_side: Side,
    size: float,
    stop_price: float,
    client_oid: str,
) -> dict[str, Any]:
    """Stop-loss: market execution, triggered on mark price, reduceOnly."""
    return {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "isolated",
        "side": _close_side(position_side),
        "orderType": "market",
        "size": size,
        "reduceOnly": True,
        "hedged": False,
        "stopPrice": stop_price,
        "triggerType": TRIGGER_TYPE_MARK,
        "clientOid": client_oid,
        "orderRole": OrderRole.SL.value,
    }
