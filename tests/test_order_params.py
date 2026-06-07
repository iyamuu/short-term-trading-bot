from __future__ import annotations

import pytest
from src.execution import bitget_client as bc
from src.execution.bitget_client import TRIGGER_TYPE_MARK
from src.storage.models import OrderRole, Side


def test_entry_is_not_reduce_only():
    p = bc.build_entry_order(symbol="BTCUSDT", side=Side.LONG, size=0.01, client_oid="co1")
    assert p["reduceOnly"] is False
    assert p["hedged"] is False
    assert p["side"] == "buy"
    assert p["clientOid"] == "co1"
    assert p["orderType"] == "market"


def test_entry_short_sells():
    p = bc.build_entry_order(symbol="BTCUSDT", side=Side.SHORT, size=0.01,
                             client_oid="co1", price=99.5)
    assert p["side"] == "sell"
    assert p["orderType"] == "limit"
    assert p["price"] == 99.5


def test_tp_is_reduce_only_and_closes():
    p = bc.build_reduce_order(symbol="BTCUSDT", position_side=Side.LONG, size=0.01,
                              client_oid="tp1", role=OrderRole.TP1, price=103.0)
    assert p["reduceOnly"] is True
    assert p["side"] == "sell"            # closing a LONG sells
    assert p["orderRole"] == "TP1"


def test_reduce_order_rejects_non_tp_role():
    with pytest.raises(ValueError):
        bc.build_reduce_order(symbol="BTCUSDT", position_side=Side.LONG, size=0.01,
                              client_oid="x", role=OrderRole.SL)


def test_stop_loss_spec_pinned():
    p = bc.build_stop_loss_order(symbol="BTCUSDT", position_side=Side.LONG, size=0.01,
                                 stop_price=99.0, client_oid="sl1")
    assert p["orderType"] == "market"
    assert p["stopPrice"] == 99.0
    assert p["triggerType"] == TRIGGER_TYPE_MARK
    assert p["reduceOnly"] is True
    assert p["hedged"] is False
    assert p["side"] == "sell"            # SL on a LONG sells
    assert p["clientOid"] == "sl1"


def test_stop_loss_short_buys():
    p = bc.build_stop_loss_order(symbol="BTCUSDT", position_side=Side.SHORT, size=0.01,
                                 stop_price=101.0, client_oid="sl2")
    assert p["side"] == "buy"             # SL on a SHORT buys
    assert p["reduceOnly"] is True
