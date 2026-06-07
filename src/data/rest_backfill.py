"""Historical market-data backfill via CCXT **public** REST (no auth, no orders).

Fetches Bitget BTCUSDT OHLCV (and funding-rate history) and writes them to
date-partitioned parquet, reusing ``storage.parquet_writer.append_rows`` for idempotent
writes. This is the data foundation the event backtester (PR #4) will read.

Scope: public endpoints only — API keys are NOT required and NOT used. WebSocket,
private API, order placement and candle building are out of scope (later phases).

The ``exchange`` handle is injected into every function so tests can pass a synthetic
fake instead of touching the network.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from ..storage.parquet_writer import append_rows

# timeframe -> milliseconds
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
}

OHLCV_COLUMNS = (
    "candle_id", "timestamp", "open_time", "open", "high", "low", "close",
    "volume", "symbol", "timeframe",
)
FUNDING_COLUMNS = ("funding_id", "timestamp", "time_iso", "symbol", "funding_rate")


class Exchange(Protocol):
    """Minimal CCXT surface we rely on (lets tests inject a fake)."""

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None, limit: int | None
    ) -> list[list[float]]: ...


# --------------------------------------------------------------------------- helpers
def to_ccxt_symbol(symbol: str, product_type: str = "USDT-FUTURES") -> str:
    """``BTCUSDT`` (+ USDT-FUTURES) -> CCXT unified ``BTC/USDT:USDT`` (USDT perp)."""
    up = symbol.upper()
    if up.endswith("USDT"):
        base = up[:-4]
        return f"{base}/USDT:USDT"
    raise ValueError(f"unsupported symbol for ccxt mapping: {symbol} ({product_type})")


def dataset_name(kind: str, symbol: str, timeframe: str | None = None) -> str:
    """Single source of truth for parquet dataset paths (Hive-style, sans ``dt=``).

    The ``dt=YYYY-MM-DD`` partition is appended by ``parquet_writer.append_rows``.
    """
    if kind == "ohlcv":
        if timeframe is None:
            raise ValueError("timeframe required for ohlcv dataset")
        return f"market/ohlcv/symbol={symbol}/timeframe={timeframe}"
    if kind == "funding":
        return f"market/funding/symbol={symbol}"
    raise ValueError(f"unknown dataset kind: {kind}")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _interval_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return TIMEFRAME_MS[timeframe]


# ----------------------------------------------------------------------- normalize
def normalize_ohlcv(
    raw: list[list[float]], symbol: str, timeframe: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candle in raw:
        ts = int(candle[0])
        rows.append(
            {
                "candle_id": f"{symbol}:{timeframe}:{ts}",
                "timestamp": ts,
                "open_time": _iso(ts),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
                "symbol": symbol,
                "timeframe": timeframe,
            }
        )
    return rows


# -------------------------------------------------------------------------- fetch
def fetch_ohlcv_range(
    exchange: Exchange,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int | None = None,
    page_limit: int = 1000,
) -> list[dict[str, Any]]:
    """Page through fetch_ohlcv from ``since_ms`` until exhausted or ``until_ms``.

    CCXT returns candles at/after ``since`` up to ``limit`` — the final page can spill
    past ``until_ms``, so anything with ``timestamp > until_ms`` is dropped here.
    """
    interval = _interval_ms(timeframe)
    out: list[dict[str, Any]] = []
    cursor = since_ms
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, cursor, page_limit)
        if not batch:
            break
        rows = normalize_ohlcv(batch, symbol, timeframe)
        if until_ms is not None:
            rows = [r for r in rows if r["timestamp"] <= until_ms]
        out.extend(rows)

        last_ts = int(batch[-1][0])
        if until_ms is not None and last_ts >= until_ms:
            break
        if len(batch) < page_limit:
            break
        next_cursor = last_ts + interval
        if next_cursor <= cursor:  # no forward progress -> stop (defensive)
            break
        cursor = next_cursor
    return out


def normalize_funding(raw: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in raw:
        ts = int(item["timestamp"])
        rate = item.get("fundingRate")
        rows.append(
            {
                "funding_id": f"{symbol}:{ts}",
                "timestamp": ts,
                "time_iso": _iso(ts),
                "symbol": symbol,
                "funding_rate": None if rate is None else float(rate),
            }
        )
    return rows


# --------------------------------------------------------------------- validation
def validate_ohlcv_rows(rows: list[dict[str, Any]], timeframe: str) -> None:
    """Schema + sanity checks. Raises ValueError on the first violation."""
    _interval_ms(timeframe)  # validates timeframe
    prev_ts: int | None = None
    for r in rows:
        for col in OHLCV_COLUMNS:
            if col not in r:
                raise ValueError(f"ohlcv row missing column: {col}")
        ts = r["timestamp"]
        if prev_ts is not None and ts <= prev_ts:
            raise ValueError(f"timestamps not strictly increasing at {ts}")
        prev_ts = ts
        o, h, low, c, v = r["open"], r["high"], r["low"], r["close"], r["volume"]
        if h < max(o, c) or low > min(o, c) or h < low:
            raise ValueError(f"OHLC sanity violation at {ts}: o={o} h={h} l={low} c={c}")
        if v < 0:
            raise ValueError(f"negative volume at {ts}: {v}")


def detect_gaps(
    rows: list[dict[str, Any]], timeframe: str
) -> list[tuple[int, int, int]]:
    """Report missing intervals as ``(gap_start_ms, gap_end_ms, missing_count)``.

    Reports only — does not raise. ``rows`` must be timestamp-ascending.
    """
    interval = _interval_ms(timeframe)
    gaps: list[tuple[int, int, int]] = []
    prev: int | None = None
    for r in rows:
        ts = r["timestamp"]
        if prev is not None:
            delta = ts - prev
            if delta > interval:
                missing = delta // interval - 1
                gaps.append((prev + interval, ts - interval, int(missing)))
        prev = ts
    return gaps


# ----------------------------------------------------------------- incremental read
def latest_partition_path(base_dir: str | Path, name: str) -> Path | None:
    """Path to the most recent ``dt=`` partition file, or None if none exist."""
    root = Path(base_dir) / name
    if not root.exists():
        return None
    parts = sorted(p for p in root.glob("dt=*") if p.is_dir())
    if not parts:
        return None
    data = parts[-1] / "data.parquet"  # dt=YYYY-MM-DD sorts chronologically
    return data if data.exists() else None


def last_stored_timestamp(base_dir: str | Path, name: str) -> int | None:
    """Max ``timestamp`` in the latest partition only (avoids full-dataset scan)."""
    path = latest_partition_path(base_dir, name)
    if path is None:
        return None
    df = pd.read_parquet(path, columns=["timestamp"])
    if df.empty:
        return None
    return int(df["timestamp"].max())


# ------------------------------------------------------------------------ backfill
@dataclass
class OhlcvBackfillResult:
    dataset: str
    rows_written: int = 0
    fetched: int = 0
    gaps: list[tuple[int, int, int]] = field(default_factory=list)
    from_ts: int | None = None
    to_ts: int | None = None


@dataclass
class FundingBackfillResult:
    dataset: str
    funding_supported: bool = True
    funding_error: str | None = None
    rows_written: int = 0


def backfill_ohlcv(
    base_dir: str | Path,
    exchange: Exchange,
    symbol: str,
    timeframe: str,
    since_ms: int | None = None,
    until_ms: int | None = None,
) -> OhlcvBackfillResult:
    """Incrementally backfill OHLCV into parquet. Resumes after the last stored ts."""
    name = dataset_name("ohlcv", symbol, timeframe)
    result = OhlcvBackfillResult(dataset=name)

    if since_ms is None:
        last = last_stored_timestamp(base_dir, name)
        since_ms = (last + _interval_ms(timeframe)) if last is not None else 0

    rows = fetch_ohlcv_range(exchange, symbol, timeframe, since_ms, until_ms)
    result.fetched = len(rows)
    if not rows:
        return result

    validate_ohlcv_rows(rows, timeframe)
    result.gaps = detect_gaps(rows, timeframe)
    result.from_ts = rows[0]["timestamp"]
    result.to_ts = rows[-1]["timestamp"]
    result.rows_written = append_rows(
        base_dir, name, rows, id_field="candle_id", ts_field="open_time"
    )
    return result


def backfill_funding(
    base_dir: str | Path,
    exchange: Any,
    symbol: str,
    since_ms: int | None = None,
    page_limit: int = 100,
) -> FundingBackfillResult:
    """Backfill funding-rate history. Never breaks the OHLCV path: on any error the
    result carries ``funding_supported=False`` + ``funding_error`` instead of raising."""
    name = dataset_name("funding", symbol)
    result = FundingBackfillResult(dataset=name)
    try:
        fetch = getattr(exchange, "fetch_funding_rate_history", None)
        if fetch is None:
            raise NotImplementedError("exchange has no fetch_funding_rate_history")
        raw = fetch(symbol, since_ms, page_limit)
        rows = normalize_funding(raw, symbol)
        result.rows_written = append_rows(
            base_dir, name, rows, id_field="funding_id", ts_field="time_iso"
        )
    except Exception as exc:  # noqa: BLE001 — funding is best-effort by design
        result.funding_supported = False
        result.funding_error = f"{type(exc).__name__}: {exc}"
    return result


# ----------------------------------------------------------------------------- CLI
def build_exchange() -> Any:
    """Public CCXT Bitget handle (no credentials). Imported lazily for testability."""
    import ccxt  # noqa: PLC0415

    return ccxt.bitget({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def _main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Bitget public OHLCV/funding backfill (no API key)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--product-type", default="USDT-FUTURES")
    ap.add_argument("--timeframe", default="1m", choices=sorted(TIMEFRAME_MS))
    ap.add_argument("--days", type=int, default=1, help="lookback window when --since omitted")
    ap.add_argument("--since", type=int, default=None, help="start epoch ms")
    ap.add_argument("--until", type=int, default=None, help="end epoch ms")
    ap.add_argument("--base-dir", default="./data")
    ap.add_argument("--with-funding", action="store_true")
    args = ap.parse_args(argv)

    exchange = build_exchange()
    ccxt_symbol = to_ccxt_symbol(args.symbol, args.product_type)

    since = args.since
    if since is None and args.days:
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        since = now_ms - args.days * 86_400_000

    res = backfill_ohlcv(args.base_dir, exchange, ccxt_symbol, args.timeframe, since, args.until)
    print(f"[ohlcv] dataset={res.dataset}")
    print(f"[ohlcv] fetched={res.fetched} written={res.rows_written} "
          f"range=[{res.from_ts}..{res.to_ts}] gaps={len(res.gaps)}")
    for g in res.gaps[:10]:
        print(f"  gap: {_iso(g[0])} .. {_iso(g[1])} (missing {g[2]})")

    if args.with_funding:
        fr = backfill_funding(args.base_dir, exchange, ccxt_symbol)
        print(f"[funding] supported={fr.funding_supported} written={fr.rows_written} "
              f"error={fr.funding_error}")


if __name__ == "__main__":
    _main()
