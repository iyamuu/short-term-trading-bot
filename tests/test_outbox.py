from __future__ import annotations

import pandas as pd
from src.storage import outbox

from conftest import make_candidate, make_trade


def _trade_files(base_dir):
    return list(base_dir.glob("trade_log/dt=*/*.parquet"))


def test_close_parks_in_outbox_then_flush(store, base_dir):
    store.open_trade(trade_id="t1", side="LONG", entry_price=100.0,
                     contracts=0.01, initial_sl=99.0)
    store.close_trade(make_trade(trade_id="t1"))
    store.record_candidate(make_candidate())

    # before flush: nothing in parquet, rows pending
    assert not _trade_files(base_dir)
    assert len(store.pending_outbox()) == 2

    res = outbox.flush(store, base_dir)
    assert res.ok
    assert res.total_written == 2
    assert _trade_files(base_dir)
    assert store.pending_outbox() == []


def test_flush_idempotent(store, base_dir):
    store.close_trade(make_trade(trade_id="t1"))
    outbox.flush(store, base_dir)
    # second flush writes nothing and does not duplicate
    res2 = outbox.flush(store, base_dir)
    assert res2.total_written == 0
    df = pd.read_parquet(_trade_files(base_dir)[0])
    assert len(df) == 1


def test_flush_failure_then_retry(store, base_dir, monkeypatch):
    store.close_trade(make_trade(trade_id="t1"))

    calls = {"n": 0}
    real_append = outbox.append_rows

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(outbox, "append_rows", flaky)

    res1 = outbox.flush(store, base_dir)
    assert not res1.ok
    assert res1.spilled == 1
    # row left unflushed for retry, and spilled to fallback JSONL
    assert len(store.pending_outbox()) == 1
    assert (base_dir / "fallback" / "trade.jsonl").exists()

    res2 = outbox.flush(store, base_dir)  # second call succeeds
    assert res2.ok
    assert store.pending_outbox() == []
    df = pd.read_parquet(_trade_files(base_dir)[0])
    assert len(df) == 1


def test_reintegrate_fallback_dedups(store, base_dir, monkeypatch):
    store.close_trade(make_trade(trade_id="t1"))

    def always_fail(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(outbox, "append_rows", always_fail)
    outbox.flush(store, base_dir)  # spills to fallback
    assert (base_dir / "fallback" / "trade.jsonl").exists()

    # restore real append, reintegrate from fallback
    monkeypatch.undo()
    written = outbox.reintegrate_fallback(base_dir)
    assert written == 1
    # re-running reintegrate must not duplicate
    assert outbox.reintegrate_fallback(base_dir) == 0
