from __future__ import annotations

import pytest
from src.data import rest_backfill as rb


def _write(base_dir, symbol, timeframe, n, start=0):
    candles = [[start + i * rb.TIMEFRAME_MS[timeframe], 100.0, 101.0, 99.0, 100.0, 1.0]
               for i in range(n)]
    rows = rb.normalize_ohlcv(candles, symbol, timeframe)
    name = rb.dataset_name("ohlcv", symbol, timeframe)
    from src.storage.parquet_writer import append_rows
    append_rows(base_dir, name, rows, id_field="candle_id", ts_field="open_time")


def test_load_ohlcv_ascending_and_filtered(base_dir):
    _write(base_dir, "BTCUSDT", "1m", 10)
    rows = rb.load_ohlcv(base_dir, "BTCUSDT", "1m")
    assert len(rows) == 10
    ts = [r["timestamp"] for r in rows]
    assert ts == sorted(ts)

    window = rb.load_ohlcv(base_dir, "BTCUSDT", "1m",
                           since_ms=2 * rb.TIMEFRAME_MS["1m"], until_ms=5 * rb.TIMEFRAME_MS["1m"])
    assert [r["timestamp"] for r in window] == [i * rb.TIMEFRAME_MS["1m"] for i in range(2, 6)]


def test_load_ohlcv_empty(base_dir):
    assert rb.load_ohlcv(base_dir, "BTCUSDT", "1m") == []


def test_load_ohlcv_symbol_mismatch_raises(base_dir, monkeypatch):
    # write under BTCUSDT path but with rows carrying the wrong symbol
    candles = [[0, 100.0, 101.0, 99.0, 100.0, 1.0]]
    rows = rb.normalize_ohlcv(candles, "ETHUSDT", "1m")  # wrong symbol in rows
    name = rb.dataset_name("ohlcv", "BTCUSDT", "1m")      # but stored under BTCUSDT
    from src.storage.parquet_writer import append_rows
    append_rows(base_dir, name, rows, id_field="candle_id", ts_field="open_time")
    with pytest.raises(ValueError):
        rb.load_ohlcv(base_dir, "BTCUSDT", "1m")
