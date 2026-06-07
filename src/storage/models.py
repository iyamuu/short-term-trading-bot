"""Record schemas and enums — the single source of truth for all logging.

Design (per the approved plan):

- ``TradeRecord`` is a *summary*; ``OrderEventRecord`` / ``FillRecord`` are append-only
  *facts*. The summary's pnl/fee/slippage must be reconstructable from the facts.
- Reject taxonomy spans the whole Issue decision flow: ``reject_stage`` +
  ``reject_reason`` + free-text ``reject_detail``.
- MAE/MFE is column-split into price / pct / R (favorable & adverse), with the
  ``initial_sl`` used for the R denominator carried alongside.

Every record validates required fields on construction and round-trips through
``to_row()`` / ``from_row()`` (enums -> value, dict fields -> JSON string) so it can be
written to / read from parquet and DuckDB unchanged.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field, fields
from enum import Enum, StrEnum
from typing import Any


# --------------------------------------------------------------------------- enums
class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(StrEnum):
    HIT_TP1 = "HIT_TP1"
    HIT_TP2 = "HIT_TP2"
    HIT_SL = "HIT_SL"
    BREAKEVEN_SL = "BREAKEVEN_SL"
    TRAILING_SL = "TRAILING_SL"
    TIME_EXIT = "TIME_EXIT"
    MANUAL = "MANUAL"
    LIQUIDATION = "LIQUIDATION"


class Regime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    LOW_VOL_CHOP = "LOW_VOL_CHOP"
    HIGH_VOL_RISK = "HIGH_VOL_RISK"


class RejectStage(StrEnum):
    """Where in the decision flow the candidate was dropped (Issue decision flow)."""

    DATA_QUALITY = "DATA_QUALITY"
    GLOBAL_STOP = "GLOBAL_STOP"
    REGIME = "REGIME"
    RISK_PRE_FILTER = "RISK_PRE_FILTER"
    NO_SETUP = "NO_SETUP"
    RISK_TRADE = "RISK_TRADE"
    ML_FILTER = "ML_FILTER"
    ORDER_PLAN_RISK = "ORDER_PLAN_RISK"


class RejectReason(StrEnum):
    # Phase 1 enumerated reasons
    HARD_NG = "HARD_NG"
    REGIME_NG = "REGIME_NG"
    SCORE_TOO_LOW = "SCORE_TOO_LOW"
    RISK_NG = "RISK_NG"
    SPREAD_NG = "SPREAD_NG"
    FUNDING_NG = "FUNDING_NG"
    ML_FILTER_NG = "ML_FILTER_NG"
    # decision-flow hold reasons
    DATA_QUALITY_NG = "DATA_QUALITY_NG"
    GLOBAL_STOP = "GLOBAL_STOP"
    NO_SETUP = "NO_SETUP"
    RISK_PRE_FILTER_NG = "RISK_PRE_FILTER_NG"
    RISK_TRADE_NG = "RISK_TRADE_NG"
    ORDER_PLAN_RISK_NG = "ORDER_PLAN_RISK_NG"


class OrderRole(StrEnum):
    ENTRY = "ENTRY"
    TP1 = "TP1"
    TP2 = "TP2"
    SL = "SL"


class OrderEventType(StrEnum):
    SUBMIT = "SUBMIT"
    ACK = "ACK"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    AMEND = "AMEND"


# ---------------------------------------------------------------- (de)serialization
_ENUM_FIELDS: dict[str, type[Enum]] = {}
_JSON_FIELDS: set[str] = set()


def _to_row(obj: Any) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for f in fields(obj):
        val = getattr(obj, f.name)
        if isinstance(val, Enum):
            row[f.name] = val.value
        elif isinstance(val, (dict, list)):
            row[f.name] = json.dumps(val, sort_keys=True, separators=(",", ":"))
        else:
            row[f.name] = val
    return row


def _from_row(cls: type, row: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    enum_map = getattr(cls, "_enum_fields", {})
    json_fields: set[str] = getattr(cls, "_json_fields", set())
    valid = {f.name for f in fields(cls)}
    for name in valid:
        if name not in row:
            continue
        val = row[name]
        if val is not None and name in enum_map:
            val = enum_map[name](val)
        elif name in json_fields and isinstance(val, str):
            val = json.loads(val)
        kwargs[name] = val
    return cls(**kwargs)


def _validate_required(obj: Any, required: tuple[str, ...]) -> None:
    missing = [name for name in required if getattr(obj, name) is None]
    if missing:
        raise ValueError(
            f"{type(obj).__name__} missing required field(s): {', '.join(missing)}"
        )


# -------------------------------------------------------------------- excursion calc
@dataclass
class ExcursionTracker:
    """Tracks running high/low while a position is open and derives MAE/MFE.

    Favorable = movement in the trade's direction; adverse = against it.
    R denominator = abs(entry_price - initial_sl) (risk per unit).
    """

    side: Side
    entry_price: float
    initial_sl: float
    running_high: float = field(default=0.0)
    running_low: float = field(default=0.0)
    _seen: bool = field(default=False, repr=False)

    def update(self, price: float) -> None:
        if not self._seen:
            self.running_high = price
            self.running_low = price
            self._seen = True
            return
        self.running_high = max(self.running_high, price)
        self.running_low = min(self.running_low, price)

    @property
    def _risk_per_unit(self) -> float:
        return abs(self.entry_price - self.initial_sl)

    @property
    def mfe_price(self) -> float:
        if self.side == Side.LONG:
            return max(0.0, self.running_high - self.entry_price)
        return max(0.0, self.entry_price - self.running_low)

    @property
    def mae_price(self) -> float:
        if self.side == Side.LONG:
            return max(0.0, self.entry_price - self.running_low)
        return max(0.0, self.running_high - self.entry_price)

    @property
    def mfe_pct(self) -> float:
        return self.mfe_price / self.entry_price if self.entry_price else 0.0

    @property
    def mae_pct(self) -> float:
        return self.mae_price / self.entry_price if self.entry_price else 0.0

    @property
    def mfe_r(self) -> float:
        r = self._risk_per_unit
        return self.mfe_price / r if r else 0.0

    @property
    def mae_r(self) -> float:
        r = self._risk_per_unit
        return self.mae_price / r if r else 0.0


# ------------------------------------------------------------------------- records
@dataclass
class TradeRecord:
    """Finalized trade *summary* (one row per closed trade)."""

    # identity / direction
    trade_id: str | None = None
    signal_id: str | None = None
    side: Side | None = None
    # entry
    entry_time: str | None = None
    entry_price: float | None = None
    entry_size: float | None = None
    entry_order_id: str | None = None
    entry_client_oid: str | None = None
    # exit
    exit_time: str | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    close_order_id: str | None = None
    close_client_oid: str | None = None
    # pnl / costs
    pnl_abs: float | None = None
    pnl_pct: float | None = None
    pnl_r: float | None = None
    fee_abs: float | None = None
    funding_abs: float | None = None
    slippage_estimated: float | None = None
    # levels
    initial_sl: float | None = None
    initial_tp1: float | None = None
    initial_tp2: float | None = None
    final_sl: float | None = None
    hit_tp1: bool | None = None
    hit_tp2: bool | None = None
    moved_to_breakeven: bool | None = None
    # excursions (column-split MAE/MFE; initial_sl above is the R denominator basis)
    mfe_price: float | None = None
    mae_price: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    # context
    holding_minutes: float | None = None
    regime: Regime | None = None
    setup_name: str | None = None
    entry_reason: str | None = None
    confidence: float | None = None
    ml_score: float | None = None
    risk_size_multiplier: float | None = None
    # reproducibility
    features_snapshot: dict[str, Any] = field(default_factory=dict)
    features_schema_version: str | None = None
    config_hash: str | None = None
    strategy_params_snapshot: dict[str, Any] = field(default_factory=dict)
    bot_version: str | None = None
    strategy_version: str | None = None
    model_version: str | None = None

    _enum_fields = {"side": Side, "exit_reason": ExitReason, "regime": Regime}
    _json_fields = {"features_snapshot", "strategy_params_snapshot"}
    _required = (
        "trade_id", "signal_id", "side", "entry_time", "entry_price", "entry_size",
        "exit_time", "exit_price", "exit_reason", "pnl_r", "initial_sl",
    )

    def __post_init__(self) -> None:
        _validate_required(self, self._required)

    def to_row(self) -> dict[str, Any]:
        return _to_row(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TradeRecord:
        return _from_row(cls, row)

    def apply_excursion(self, tracker: ExcursionTracker) -> None:
        """Fill the six MAE/MFE columns from a tracker."""
        self.mfe_price = tracker.mfe_price
        self.mae_price = tracker.mae_price
        self.mfe_pct = tracker.mfe_pct
        self.mae_pct = tracker.mae_pct
        self.mfe_r = tracker.mfe_r
        self.mae_r = tracker.mae_r


@dataclass
class OrderEventRecord:
    """Append-only order lifecycle *fact*."""

    event_id: str | None = None
    ts: str | None = None
    trade_id: str | None = None
    signal_id: str | None = None
    order_id: str | None = None
    client_oid: str | None = None
    order_role: OrderRole | None = None
    event_type: OrderEventType | None = None
    side: Side | None = None
    reduce_only: bool | None = None
    price: float | None = None
    stop_price: float | None = None
    trigger_type: str | None = None
    size: float | None = None
    status: str | None = None
    raw_detail: dict[str, Any] = field(default_factory=dict)

    _enum_fields = {"order_role": OrderRole, "event_type": OrderEventType, "side": Side}
    _json_fields = {"raw_detail"}
    _required = ("event_id", "ts", "trade_id", "order_role", "event_type")

    def __post_init__(self) -> None:
        _validate_required(self, self._required)

    def to_row(self) -> dict[str, Any]:
        return _to_row(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> OrderEventRecord:
        return _from_row(cls, row)


@dataclass
class FillRecord:
    """Append-only fill *fact*. Trade pnl/fee/slippage are reconstructable from these."""

    fill_id: str | None = None
    ts: str | None = None
    trade_id: str | None = None
    order_id: str | None = None
    client_oid: str | None = None
    order_role: OrderRole | None = None
    side: Side | None = None
    price: float | None = None
    size: float | None = None
    fee_abs: float | None = None
    is_maker: bool | None = None
    liquidity: str | None = None
    slippage_vs_intended: float | None = None

    _enum_fields = {"order_role": OrderRole, "side": Side}
    _json_fields = set()  # type: ignore[var-annotated]
    _required = ("fill_id", "ts", "trade_id", "order_id", "side", "price", "size")

    def __post_init__(self) -> None:
        _validate_required(self, self._required)

    def to_row(self) -> dict[str, Any]:
        return _to_row(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> FillRecord:
        return _from_row(cls, row)


@dataclass
class CandidateRecord:
    """A rule candidate that did NOT become a trade, with the reject reason."""

    candidate_id: str | None = None
    ts: str | None = None
    signal_id: str | None = None
    side: Side | None = None
    regime: Regime | None = None
    setup_name: str | None = None
    reject_stage: RejectStage | None = None
    reject_reason: RejectReason | None = None
    reject_detail: str | None = None
    confidence: float | None = None
    ml_score: float | None = None
    features_snapshot: dict[str, Any] = field(default_factory=dict)
    features_schema_version: str | None = None
    config_hash: str | None = None
    bot_version: str | None = None
    strategy_version: str | None = None

    _enum_fields = {
        "side": Side,
        "regime": Regime,
        "reject_stage": RejectStage,
        "reject_reason": RejectReason,
    }
    _json_fields = {"features_snapshot"}
    _required = ("candidate_id", "ts", "reject_stage", "reject_reason")

    def __post_init__(self) -> None:
        _validate_required(self, self._required)

    def to_row(self) -> dict[str, Any]:
        return _to_row(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CandidateRecord:
        return _from_row(cls, row)


def is_record(obj: Any) -> bool:
    return dataclasses.is_dataclass(obj) and hasattr(obj, "to_row")
