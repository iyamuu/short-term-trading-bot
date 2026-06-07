from __future__ import annotations

import dataclasses

import pandas as pd
from src.storage import trade_log
from src.storage.models import TradeRecord

from conftest import make_trade


def test_full_trade_persists_all_fields(base_dir):
    t = make_trade(features_snapshot={"rsi": 60})
    written = trade_log.append(base_dir, [t])
    assert written == 1

    files = list(base_dir.glob("trade_log/dt=*/*.parquet"))
    assert files, "expected a partitioned parquet file"
    df = pd.read_parquet(files[0])
    assert len(df) == 1

    # every TradeRecord field is present as a column
    expected = {f.name for f in dataclasses.fields(TradeRecord)}
    assert expected.issubset(set(df.columns))


def test_trade_log_idempotent(base_dir):
    t = make_trade()
    trade_log.append(base_dir, [t])
    again = trade_log.append(base_dir, [t])  # same trade_id
    assert again == 0
    files = list(base_dir.glob("trade_log/dt=*/*.parquet"))
    df = pd.read_parquet(files[0])
    assert len(df) == 1


def test_trade_log_dedups_within_batch(base_dir):
    # same trade_id passed twice in ONE append call must not duplicate
    written = trade_log.append(base_dir, [make_trade(), make_trade()])
    assert written == 1
    df = pd.read_parquet(next(base_dir.glob("trade_log/dt=*/*.parquet")))
    assert len(df) == 1
