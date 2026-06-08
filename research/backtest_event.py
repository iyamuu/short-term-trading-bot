"""Thin runner for the event backtester (Issue Phase 2).

Wires data + intents + config into ``src.backtest.engine.run_backtest`` and prints a
report. All simulation logic lives in ``src/backtest/`` — this file stays a runner.

Usage:
    python research/backtest_event.py --demo
    python research/backtest_event.py --base-dir ./data --symbol BTCUSDT --timeframe 1m
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.engine import run_backtest  # noqa: E402
from src.backtest.models import BacktestCostConfig, EntryIntent  # noqa: E402
from src.backtest.report import compare_costs  # noqa: E402
from src.config import StrategyConfig  # noqa: E402
from src.data.rest_backfill import load_ohlcv  # noqa: E402
from src.storage.models import Side  # noqa: E402

INTERVAL_1M = 60_000


def _demo_candles(n: int = 30, start: int = 0) -> list[dict]:
    """A gentle uptrend so a long entry reaches TP1 then TP2."""
    candles = []
    price = 100.0
    for i in range(n):
        o = price
        h = price + 1.0 + i * 0.1
        low = price - 0.5
        c = price + 0.2
        candles.append({
            "timestamp": start + i * INTERVAL_1M, "open": o, "high": h,
            "low": low, "close": c, "volume": 1.0,
        })
        price = c
    return candles


def _demo_intents() -> list[EntryIntent]:
    return [
        EntryIntent(
            timestamp=0, side=Side.LONG, entry_price=100.0, sl=99.0, tp1=101.0, tp2=102.0,
            size=1.0, signal_id="demo-1", setup_name="trend_pullback", confidence=0.6,
        )
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="run on synthetic candles")
    ap.add_argument("--base-dir", default="./data")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframe", default="1m")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    strat = StrategyConfig()
    cost = BacktestCostConfig()

    if args.demo:
        candles = _demo_candles()
        intents = _demo_intents()
    else:
        candles = load_ohlcv(args.base_dir, args.symbol, args.timeframe)
        if not candles:
            print(f"no OHLCV found under {args.base_dir} for {args.symbol}/{args.timeframe}; "
                  "run rest_backfill first (or use --demo)")
            return
        intents = []  # real intents come from a strategy (PR #7); none yet

    result = run_backtest(candles, intents, strat, cost)
    diff = compare_costs(candles, intents, strat, cost)

    out = {
        "trades": len(result.trades),
        "skipped": len(result.skipped),
        "metrics": result.metrics,
        "cost_impact": result.cost_impact,
        "cost_compare": diff,
    }
    text = json.dumps(out, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
