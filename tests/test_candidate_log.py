from __future__ import annotations

import pandas as pd
import pytest
from src.storage import candidate_log
from src.storage.models import RejectReason, RejectStage

from conftest import make_candidate


def test_candidate_persisted_with_reason(base_dir):
    c = make_candidate()
    assert candidate_log.append(base_dir, [c]) == 1
    df = pd.read_parquet(next(base_dir.glob("candidate_log/dt=*/*.parquet")))
    assert df.iloc[0]["reject_reason"] == "REGIME_NG"
    assert df.iloc[0]["reject_stage"] == "REGIME"


@pytest.mark.parametrize(
    "stage,reason",
    [
        (RejectStage.DATA_QUALITY, RejectReason.DATA_QUALITY_NG),
        (RejectStage.GLOBAL_STOP, RejectReason.GLOBAL_STOP),
        (RejectStage.REGIME, RejectReason.REGIME_NG),
        (RejectStage.RISK_PRE_FILTER, RejectReason.SPREAD_NG),
        (RejectStage.RISK_PRE_FILTER, RejectReason.FUNDING_NG),
        (RejectStage.NO_SETUP, RejectReason.NO_SETUP),
        (RejectStage.RISK_TRADE, RejectReason.RISK_TRADE_NG),
        (RejectStage.ML_FILTER, RejectReason.ML_FILTER_NG),
        (RejectStage.ORDER_PLAN_RISK, RejectReason.ORDER_PLAN_RISK_NG),
    ],
)
def test_decision_flow_reject_taxonomy(base_dir, stage, reason):
    c = make_candidate(candidate_id=f"c-{stage.value}-{reason.value}",
                       reject_stage=stage, reject_reason=reason)
    assert candidate_log.append(base_dir, [c]) == 1


def test_candidate_log_idempotent(base_dir):
    c = make_candidate()
    candidate_log.append(base_dir, [c])
    assert candidate_log.append(base_dir, [c]) == 0
