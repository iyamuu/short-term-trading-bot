from __future__ import annotations

import pytest
from src.storage.models import Side

from conftest import make_trade


def test_open_attach_close_lifecycle(store):
    store.open_trade(
        trade_id="t1", side=Side.LONG, entry_price=100.0, contracts=0.01, initial_sl=99.0,
    )
    assert store.get_open_position("t1") is not None
    store.attach_orders("t1", [
        {"order_role": "ENTRY", "order_id": "o1", "client_oid": "co1", "reduce_only": False},
        {"order_role": "SL", "order_id": "o2", "client_oid": "co2", "reduce_only": True},
    ])
    assert len(store.get_open_orders("t1")) == 2

    store.close_trade(make_trade(trade_id="t1"))
    # state cleared
    assert store.get_open_position("t1") is None
    assert store.get_open_orders("t1") == []
    # summary parked in outbox
    pending = store.pending_outbox()
    assert any(p["kind"] == "TRADE" for p in pending)


def test_second_open_position_rejected(store):
    store.open_trade(trade_id="t1", side=Side.LONG, entry_price=100.0,
                     contracts=0.01, initial_sl=99.0)
    # one-way mode: opening a second concurrent position must be rejected
    with pytest.raises(ValueError):
        store.open_trade(trade_id="t2", side=Side.SHORT, entry_price=100.0,
                         contracts=0.01, initial_sl=101.0)
    # after closing the first, a new one is allowed
    store.close_trade(make_trade(trade_id="t1"))
    store.open_trade(trade_id="t2", side=Side.SHORT, entry_price=100.0,
                     contracts=0.01, initial_sl=101.0)
    assert store.get_open_position("t2") is not None


def test_short_contracts_positive(store):
    store.open_trade(
        trade_id="t2", side=Side.SHORT, entry_price=100.0, contracts=0.5, initial_sl=101.0,
    )
    pos = store.get_open_position("t2")
    assert pos["side"] == "SHORT"
    assert pos["contracts"] == 0.5  # positive even for SHORT


def test_excursion_applied_on_close(store):
    store.open_trade(
        trade_id="t3", side=Side.LONG, entry_price=100.0, contracts=0.01, initial_sl=98.0,
    )
    store.update_excursion("t3", 100.0)
    store.update_excursion("t3", 106.0)
    store.update_excursion("t3", 97.0)
    rec = make_trade(trade_id="t3", initial_sl=98.0)
    store.close_trade(rec)
    assert rec.mfe_price == 6.0
    assert rec.mae_price == 3.0
    assert rec.mfe_r == 3.0  # 6 / |100-98|
