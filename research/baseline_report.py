"""Baseline aggregation over the parquet logs (Phase 0 deliverable).

Reads the trade and candidate parquet datasets via DuckDB and reports the metrics the
Issue asks for: trade count, win rate, avg win/loss, profit factor, max drawdown,
average R, MAE/MFE, and regime / setup / hour breakdowns — PLUS candidate-side
analysis (reject reason counts, rejection rate by setup/regime), so the logging phase
has standalone value. Output is Markdown + JSON. Empty inputs yield a safe zero report.

Usage:
    python research/baseline_report.py [--base-dir DIR] [--out PATH] [--demo]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load(base_dir: str | Path, name: str) -> pd.DataFrame:
    glob = Path(base_dir) / name / "dt=*" / "*.parquet"
    matches = list(Path(base_dir).glob(f"{name}/dt=*/*.parquet"))
    if not matches:
        return pd.DataFrame()
    return duckdb.sql(
        f"SELECT * FROM read_parquet('{glob.as_posix()}')"
    ).df()


def _f(value: Any) -> float:
    """NA/None-safe float (empty or all-NA aggregates -> 0.0)."""
    if value is None or pd.isna(value):
        return 0.0
    return float(value)


def _max_drawdown(pnl_sorted: pd.Series) -> float:
    if pnl_sorted.empty:
        return 0.0
    equity = pnl_sorted.cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    return float(drawdown.min())  # <= 0


def _breakdown(trades: pd.DataFrame, key: str) -> list[dict[str, Any]]:
    if trades.empty or key not in trades:
        return []
    out = []
    for val, grp in trades.groupby(key, dropna=False):
        pnl = grp["pnl_abs"]
        out.append(
            {
                key: None if pd.isna(val) else val,
                "trade_count": int(len(grp)),
                "win_rate": _f((pnl > 0).mean()) if len(grp) else 0.0,
                "net_pnl": _f(pnl.sum()),
                "avg_r": _f(grp["pnl_r"].mean()) if "pnl_r" in grp else 0.0,
            }
        )
    return out


def compute_baseline(base_dir: str | Path) -> dict[str, Any]:
    trades = _load(base_dir, "trade_log")
    candidates = _load(base_dir, "candidate_log")

    report: dict[str, Any] = {"trades": {}, "candidates": {}}

    # ---- trade-side
    t: dict[str, Any] = {"trade_count": int(len(trades))}
    if not trades.empty:
        pnl = trades["pnl_abs"]
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        t["win_rate"] = _f((pnl > 0).mean())
        t["avg_win"] = _f(wins.mean()) if len(wins) else 0.0
        t["avg_loss"] = _f(losses.mean()) if len(losses) else 0.0
        gross_loss = abs(_f(losses.sum()))
        t["profit_factor"] = _f(wins.sum()) / gross_loss if gross_loss else float("inf")
        t["net_pnl"] = _f(pnl.sum())
        t["average_r"] = _f(trades["pnl_r"].mean()) if "pnl_r" in trades else 0.0
        t["median_r"] = _f(trades["pnl_r"].median()) if "pnl_r" in trades else 0.0
        if "exit_time" in trades:
            ordered = trades.sort_values("exit_time")["pnl_abs"]
            t["max_drawdown"] = _max_drawdown(ordered)
        t["avg_mfe_r"] = _f(trades["mfe_r"].mean()) if "mfe_r" in trades else 0.0
        t["avg_mae_r"] = _f(trades["mae_r"].mean()) if "mae_r" in trades else 0.0
        if "entry_time" in trades:
            hours = pd.to_datetime(trades["entry_time"], utc=True, errors="coerce").dt.hour
            trades = trades.assign(_hour=hours)
        t["by_regime"] = _breakdown(trades, "regime")
        t["by_setup"] = _breakdown(trades, "setup_name")
        t["by_hour"] = _breakdown(trades, "_hour")
        t["by_side"] = _breakdown(trades, "side")
    report["trades"] = t

    # ---- candidate-side
    c: dict[str, Any] = {"candidate_count": int(len(candidates))}
    if not candidates.empty:
        c["by_reject_reason"] = (
            candidates["reject_reason"].value_counts().to_dict()
            if "reject_reason" in candidates else {}
        )
        c["by_reject_stage"] = (
            candidates["reject_stage"].value_counts().to_dict()
            if "reject_stage" in candidates else {}
        )
        c["rejection_rate_by_setup"] = _rejection_rate(trades, candidates, "setup_name")
        c["rejection_rate_by_regime"] = _rejection_rate(trades, candidates, "regime")
    report["candidates"] = c

    return report


def _rejection_rate(
    trades: pd.DataFrame, candidates: pd.DataFrame, key: str
) -> dict[str, float]:
    if candidates.empty or key not in candidates:
        return {}
    accepted = trades[key].value_counts().to_dict() if (not trades.empty and key in trades) else {}
    rejected = candidates[key].value_counts().to_dict()
    keys = set(accepted) | set(rejected)
    out: dict[str, float] = {}
    for k in keys:
        a = accepted.get(k, 0)
        r = rejected.get(k, 0)
        total = a + r
        out[str(k)] = float(r / total) if total else 0.0
    return out


def render_markdown(report: dict[str, Any]) -> str:
    t = report["trades"]
    c = report["candidates"]
    lines = ["# Baseline Report", ""]
    lines.append("## Trades")
    if t.get("trade_count", 0) == 0:
        lines.append("_No trades recorded yet._")
    else:
        lines += [
            f"- trade_count: {t['trade_count']}",
            f"- win_rate: {t['win_rate']:.3f}",
            f"- net_pnl: {t['net_pnl']:.4f}",
            f"- profit_factor: {t['profit_factor']:.3f}",
            f"- average_r: {t['average_r']:.3f} (median {t['median_r']:.3f})",
            f"- max_drawdown: {t.get('max_drawdown', 0.0):.4f}",
            f"- avg MFE/MAE (R): {t['avg_mfe_r']:.3f} / {t['avg_mae_r']:.3f}",
        ]
        for label, bk in (("Regime", "by_regime"), ("Setup", "by_setup"), ("Hour", "by_hour")):
            rows = t.get(bk, [])
            if rows:
                lines.append(f"\n### By {label}")
                for row in rows:
                    name = next(iter(row.values()))
                    lines.append(
                        f"- {name}: n={row['trade_count']} win={row['win_rate']:.2f} "
                        f"pnl={row['net_pnl']:.2f} avgR={row['avg_r']:.2f}"
                    )
    lines.append("\n## Candidates (rejected)")
    if c.get("candidate_count", 0) == 0:
        lines.append("_No candidates recorded yet._")
    else:
        lines.append(f"- candidate_count: {c['candidate_count']}")
        lines.append(f"- by_reject_reason: {c.get('by_reject_reason', {})}")
        lines.append(f"- by_reject_stage: {c.get('by_reject_stage', {})}")
        lines.append(f"- rejection_rate_by_setup: {c.get('rejection_rate_by_setup', {})}")
        lines.append(f"- rejection_rate_by_regime: {c.get('rejection_rate_by_regime', {})}")
    return "\n".join(lines) + "\n"


def generate_demo(base_dir: str | Path) -> None:
    """Push a handful of dummy trades + candidates through the real storage path."""
    from src.storage import outbox
    from src.storage.models import (
        CandidateRecord,
        ExitReason,
        Regime,
        RejectReason,
        RejectStage,
        Side,
        TradeRecord,
    )
    from src.storage.state_store import StateStore

    base = Path(base_dir)
    store = StateStore(base / "state.db")
    # columns: side, entry, sl, exit, r, exit_reason, regime, setup, time
    demo_trades = [
        ("LONG", 100.0, 99.0, 103.0, 3.0, ExitReason.HIT_TP2,
         Regime.TREND_UP, "trend_pullback", "2026-06-01T02:00:00Z"),
        ("LONG", 100.0, 99.0, 98.5, -1.0, ExitReason.HIT_SL,
         Regime.RANGE, "breakout", "2026-06-01T09:00:00Z"),
        ("SHORT", 100.0, 101.0, 97.0, 3.0, ExitReason.HIT_TP2,
         Regime.TREND_DOWN, "trend_pullback", "2026-06-02T14:00:00Z"),
        ("SHORT", 100.0, 101.0, 101.0, -1.0, ExitReason.HIT_SL,
         Regime.HIGH_VOL_RISK, "breakout", "2026-06-02T22:00:00Z"),
    ]
    for i, (side, entry, sl, exitp, r, reason, regime, setup, t) in enumerate(demo_trades):
        tid = f"demo-trade-{i}"
        s = Side(side)
        store.open_trade(
            trade_id=tid, side=s, entry_price=entry, contracts=0.01, initial_sl=sl,
            signal_id=f"sig-{i}", entry_time=t, regime=regime.value, setup_name=setup,
        )
        # simulate excursion
        store.update_excursion(tid, exitp)
        store.update_excursion(tid, entry)
        rec = TradeRecord(
            trade_id=tid, signal_id=f"sig-{i}", side=s, entry_time=t, entry_price=entry,
            entry_size=0.01, exit_time=t, exit_price=exitp, exit_reason=reason,
            pnl_abs=(exitp - entry) * (1 if s == Side.LONG else -1) * 0.01,
            pnl_pct=0.0, pnl_r=r, initial_sl=sl, regime=regime, setup_name=setup,
        )
        store.close_trade(rec)

    demo_candidates = [
        (RejectStage.REGIME, RejectReason.REGIME_NG, "trend_pullback", Regime.LOW_VOL_CHOP),
        (RejectStage.RISK_PRE_FILTER, RejectReason.SPREAD_NG, "breakout",
         Regime.HIGH_VOL_RISK),
        (RejectStage.ML_FILTER, RejectReason.ML_FILTER_NG, "trend_pullback", Regime.TREND_UP),
    ]
    for i, (stage, reason, setup, regime) in enumerate(demo_candidates):
        store.record_candidate(
            CandidateRecord(
                candidate_id=f"demo-cand-{i}", ts="2026-06-01T03:00:00Z",
                reject_stage=stage, reject_reason=reason, setup_name=setup, regime=regime,
            )
        )

    outbox.flush(store, base)
    store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default="./data")
    ap.add_argument("--out", default=None, help="write Markdown here (default: stdout)")
    ap.add_argument("--demo", action="store_true", help="seed dummy data first")
    args = ap.parse_args()

    if args.demo:
        generate_demo(args.base_dir)

    report = compute_baseline(args.base_dir)
    md = render_markdown(report)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        Path(args.out).with_suffix(".json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        print(f"wrote {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
