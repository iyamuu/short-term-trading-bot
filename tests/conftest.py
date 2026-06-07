"""Shared fixtures: isolated tmp data dir + state store per test."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.storage.models import (  # noqa: E402
    CandidateRecord,
    ExitReason,
    Regime,
    RejectReason,
    RejectStage,
    Side,
    TradeRecord,
)
from src.storage.state_store import StateStore  # noqa: E402


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def store(base_dir: Path) -> StateStore:
    s = StateStore(base_dir / "state.db")
    yield s
    s.close()


def make_trade(**overrides) -> TradeRecord:
    defaults = dict(
        trade_id="t1",
        signal_id="s1",
        side=Side.LONG,
        entry_time="2026-06-01T00:00:00Z",
        entry_price=100.0,
        entry_size=0.01,
        exit_time="2026-06-01T01:00:00Z",
        exit_price=102.0,
        exit_reason=ExitReason.HIT_TP2,
        pnl_abs=0.02,
        pnl_pct=0.02,
        pnl_r=2.0,
        initial_sl=99.0,
        regime=Regime.TREND_UP,
        setup_name="trend_pullback",
    )
    defaults.update(overrides)
    return TradeRecord(**defaults)


def make_candidate(**overrides) -> CandidateRecord:
    defaults = dict(
        candidate_id="c1",
        ts="2026-06-01T00:00:00Z",
        reject_stage=RejectStage.REGIME,
        reject_reason=RejectReason.REGIME_NG,
        setup_name="trend_pullback",
        regime=Regime.LOW_VOL_CHOP,
    )
    defaults.update(overrides)
    return CandidateRecord(**defaults)
