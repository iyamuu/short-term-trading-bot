"""Configuration: runtime/connection settings and typed trading parameters.

Two layers are kept separate on purpose:

- ``Settings`` — environment/connection (API keys, paths, env name). Loaded from
  ``.env`` via pydantic-settings. Secrets never live in the repo.
- ``StrategyConfig`` — typed trading parameters (risk, SL/TP, thresholds). Kept in
  one typed object so changes are easy to diff/track (Issue Phase 7: "record param
  changes"). ``config_hash()`` produces a stable hash stored alongside each trade so
  results stay reproducible.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Bumped whenever the feature snapshot layout changes; stored on every trade.
FEATURES_SCHEMA_VERSION = "1"
BOT_VERSION = "0.1.0"
STRATEGY_VERSION = "0.1.0"


class Settings(BaseSettings):
    """Environment/connection settings, loaded from ``.env`` (prefix ``BOT_``)."""

    model_config = SettingsConfigDict(
        env_prefix="BOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bitget_api_key: str = ""
    bitget_api_secret: str = ""
    bitget_api_passphrase: str = ""

    env: str = "paper"  # paper | demo | live
    symbol: str = "BTCUSDT"
    product_type: str = "USDT-FUTURES"

    data_dir: str = "./data"
    state_db: str = "./data/state.db"
    discord_webhook: str = ""


class StrategyConfig(BaseModel):
    """Typed trading parameters. Hashed into every trade for reproducibility."""

    # Risk / sizing
    risk_per_trade: float = Field(0.005, description="account fraction risked per trade")
    target_vol: float = Field(0.02, description="volatility target for vol scaling")
    max_notional_cap: float = Field(0.0, description="absolute notional cap (0 = disabled)")
    max_leverage: float = 5.0

    # ML meta filter thresholds (used in later phases; kept here for reproducibility)
    ml_skip_threshold: float = 0.53
    ml_full_size_threshold: float = 0.60

    # Exit plan
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    trailing_after_r: float = 2.0
    move_sl_to_breakeven_after_tp1: bool = True

    # No-trade filters
    max_spread_bps: float = 5.0
    funding_extreme_abs: float = 0.0005

    def config_hash(self) -> str:
        """Stable short hash of the parameter set."""
        payload = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_settings() -> Settings:
    return Settings()
