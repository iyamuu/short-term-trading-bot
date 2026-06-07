"""Offline tests for the public-REST backfill (synthetic exchange, no network)."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from src.data import rest_backfill as rb

INTERVAL = rb.TIMEFRAME_MS["1m"]
SYMBOL = "BTCUSDT"  # market symbol used for storage (NOT the CCXT unified form)


def make_candles(start_ms: int, n: int, interval: int = INTERVAL) -> list[list[float]]:
    """n valid candles (h>=max(o,c), l<=min(o,c)), ascending."""
    return [
        [start_ms + i * interval, 100.0, 101.0, 99.0, 100.0, 1.0] for i in range(n)
    ]


class FakeExchange:
    """Returns candles at/after ``since`` up to ``limit`` — mimics CCXT fetch_ohlcv."""

    def __init__(self, candles: list[list[float]]):
        self.candles = candles
        self.calls: list[tuple[int | None, int | None]] = []

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append((since, limit))
        since = since or 0
        sel = [c for c in self.candles if c[0] >= since]
        return sel[: (limit or len(sel))]


# --------------------------------------------------------------------------- helpers
def test_to_ccxt_symbol():
    assert rb.to_ccxt_symbol("BTCUSDT", "USDT-FUTURES") == "BTC/USDT:USDT"
    with pytest.raises(ValueError):
        rb.to_ccxt_symbol("BTCUSD")


def test_dataset_name_single_source():
    assert rb.dataset_name("ohlcv", "BTCUSDT", "1m") == "market/ohlcv/symbol=BTCUSDT/timeframe=1m"
    assert rb.dataset_name("funding", "BTCUSDT") == "market/funding/symbol=BTCUSDT"


def test_dataset_name_rejects_ccxt_symbol():
    # CCXT unified symbols contain '/' and ':' which would break the path layout
    with pytest.raises(ValueError):
        rb.dataset_name("ohlcv", "BTC/USDT:USDT", "1m")
    with pytest.raises(ValueError):
        rb.dataset_name("funding", "BTC/USDT:USDT")


def test_composite_id_distinguishes_symbol_timeframe():
    a = rb.normalize_ohlcv([[1000, 1, 2, 0.5, 1, 1]], "BTCUSDT", "1m")[0]
    b = rb.normalize_ohlcv([[1000, 1, 2, 0.5, 1, 1]], "ETHUSDT", "1m")[0]
    c = rb.normalize_ohlcv([[1000, 1, 2, 0.5, 1, 1]], "BTCUSDT", "5m")[0]
    assert a["candle_id"] != b["candle_id"]
    assert a["candle_id"] != c["candle_id"]


# ----------------------------------------------------------------------------- fetch
def test_pagination_assembles_full_range():
    candles = make_candles(0, 2500)
    ex = FakeExchange(candles)
    rows = rb.fetch_ohlcv_range(ex, SYMBOL, "1m", since_ms=0, page_limit=1000)
    assert len(rows) == 2500
    ts = [r["timestamp"] for r in rows]
    assert ts == sorted(ts)
    assert len(set(ts)) == 2500  # no overlap across pages


def test_until_ms_drops_overshoot():
    candles = make_candles(0, 100)
    ex = FakeExchange(candles)
    until = 49 * INTERVAL
    rows = rb.fetch_ohlcv_range(ex, SYMBOL, "1m", since_ms=0, until_ms=until, page_limit=1000)
    assert len(rows) == 50
    assert max(r["timestamp"] for r in rows) == until


# ------------------------------------------------------------------------- validate
def test_validate_rejects_bad_ohlc():
    bad = rb.normalize_ohlcv([[0, 100, 98, 99, 100, 1]], "BTCUSDT", "1m")  # high < low/close
    with pytest.raises(ValueError):
        rb.validate_ohlcv_rows(bad, "1m")


def test_validate_rejects_non_increasing():
    rows = rb.normalize_ohlcv(make_candles(0, 2), "BTCUSDT", "1m")
    rows[1]["timestamp"] = rows[0]["timestamp"]
    with pytest.raises(ValueError):
        rb.validate_ohlcv_rows(rows, "1m")


def test_detect_gaps():
    rows = rb.normalize_ohlcv(make_candles(0, 5), "BTCUSDT", "1m")
    del rows[2]  # drop one candle -> a one-candle gap
    gaps = rb.detect_gaps(rows, "1m")
    assert len(gaps) == 1
    assert gaps[0][2] == 1


# ------------------------------------------------------------------------ backfill
def test_parquet_layout_and_schema(base_dir):
    ex = FakeExchange(make_candles(0, 10))
    res = rb.backfill_ohlcv(base_dir, ex, SYMBOL, "1m", since_ms=0)
    assert res.rows_written == 10

    # the symbol token is a single literal dir, NOT split by '/'
    assert (base_dir / "market" / "ohlcv" / "symbol=BTCUSDT" / "timeframe=1m").is_dir()

    name = rb.dataset_name("ohlcv", SYMBOL, "1m")
    files = list((base_dir / name).glob("dt=*/data.parquet"))
    assert files
    df = pd.read_parquet(files[0])
    assert set(rb.OHLCV_COLUMNS).issubset(df.columns)
    assert set(df["symbol"]) == {"BTCUSDT"}


def test_idempotent_rewrite(base_dir):
    ex = FakeExchange(make_candles(0, 10))
    rb.backfill_ohlcv(base_dir, ex, SYMBOL, "1m", since_ms=0)
    res2 = rb.backfill_ohlcv(base_dir, ex, SYMBOL, "1m", since_ms=0)
    assert res2.rows_written == 0

    name = rb.dataset_name("ohlcv", SYMBOL, "1m")
    total = sum(len(pd.read_parquet(f)) for f in (base_dir / name).glob("dt=*/data.parquet"))
    assert total == 10


def test_backfill_incremental_resumes(base_dir):
    ex = FakeExchange(make_candles(0, 100))
    # first pass: only up to candle 49
    rb.backfill_ohlcv(base_dir, ex, SYMBOL, "1m", since_ms=0, until_ms=49 * INTERVAL)
    # second pass: since omitted -> resume after last stored ts
    res2 = rb.backfill_ohlcv(base_dir, ex, SYMBOL, "1m")
    assert res2.rows_written == 50
    assert res2.from_ts == 50 * INTERVAL

    name = rb.dataset_name("ohlcv", SYMBOL, "1m")
    total = sum(len(pd.read_parquet(f)) for f in (base_dir / name).glob("dt=*/data.parquet"))
    assert total == 100


def test_last_stored_timestamp_uses_latest_partition(base_dir):
    d1 = int(datetime(2026, 6, 1, 23, 59, tzinfo=UTC).timestamp() * 1000)
    candles = make_candles(d1, 2)  # spans 2026-06-01 23:59 and 2026-06-02 00:00
    ex = FakeExchange(candles)
    rb.backfill_ohlcv(base_dir, ex, SYMBOL, "1m", since_ms=d1)

    name = rb.dataset_name("ohlcv", SYMBOL, "1m")
    latest = rb.latest_partition_path(base_dir, name)
    assert latest is not None and "dt=2026-06-02" in str(latest)
    assert rb.last_stored_timestamp(base_dir, name) == d1 + INTERVAL


# -------------------------------------------------------------------------- funding
FUNDING_INTERVAL = 28_800_000  # 8h


class FundingExchange:
    """since/limit-aware funding fake (ascending), for pagination/incremental tests."""

    def __init__(self, n: int):
        self.points = [
            {"timestamp": i * FUNDING_INTERVAL, "fundingRate": 0.0001 * (1 if i % 2 else -1)}
            for i in range(n)
        ]
        self.calls: list[tuple[int | None, int | None]] = []

    def fetch_funding_rate_history(self, symbol, since, limit):
        self.calls.append((since, limit))
        since = since or 0
        sel = [p for p in self.points if p["timestamp"] >= since]
        return sel[: (limit or len(sel))]


def test_funding_happy_path(base_dir):
    res = rb.backfill_funding(base_dir, FundingExchange(2), SYMBOL)
    assert res.funding_supported is True
    assert res.rows_written == 2
    name = rb.dataset_name("funding", SYMBOL)
    df = pd.read_parquet(next((base_dir / name).glob("dt=*/data.parquet")))
    assert set(rb.FUNDING_COLUMNS).issubset(df.columns)
    assert set(df["symbol"]) == {"BTCUSDT"}


def test_funding_paginates_and_resumes(base_dir):
    ex = FundingExchange(5)
    # small page_limit forces multiple pages
    res = rb.backfill_funding(base_dir, ex, SYMBOL, page_limit=2)
    assert res.funding_supported is True
    assert res.rows_written == 5
    assert len(ex.calls) >= 3  # paged

    # incremental: second run resumes after last stored ts -> nothing new
    res2 = rb.backfill_funding(base_dir, ex, SYMBOL, page_limit=2)
    assert res2.rows_written == 0


def test_funding_unsupported_does_not_break_ohlcv(base_dir):
    ex = FakeExchange(make_candles(0, 10))  # no fetch_funding_rate_history
    ohlcv = rb.backfill_ohlcv(base_dir, ex, SYMBOL, "1m", since_ms=0)
    funding = rb.backfill_funding(base_dir, ex, SYMBOL)
    assert ohlcv.rows_written == 10            # OHLCV path unaffected
    assert funding.funding_supported is False
    assert funding.funding_error is not None
