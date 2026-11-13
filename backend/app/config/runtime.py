"""Runtime-tunable settings with database persistence.

Environment values are defaults. The dashboard can override a small, validated
subset; overrides live in the bot_settings table so they survive restarts.
Secrets are never overridable from the API.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings

OVERRIDABLE_KEYS = (
    "trigger_threshold",
    "final_window_seconds",
    "stage2_seconds",
    "stage2_threshold",
    "stage3_seconds",
    "stage3_threshold",
    "max_execution_price",
    "trade_amount_usdc",
    "max_daily_loss",
    "daily_profit_target",
    "stale_data_max_age_ms",
    "duplicate_policy",
    "conflict_policy",
    "paper_slippage_bps",
    "paper_fee_rate",
    "max_trades_per_day",
    "max_open_positions",
    "min_price_gap_usd",
)


class RuntimeSettings(BaseModel):
    trigger_threshold: float = Field(ge=0.01, le=0.999)
    final_window_seconds: int = Field(ge=1, le=300)
    stage2_seconds: int = Field(default=50, ge=1, le=300)
    stage2_threshold: float = Field(default=0.90, ge=0.01, le=0.999)
    stage3_seconds: int = Field(default=30, ge=1, le=300)
    stage3_threshold: float = Field(default=0.90, ge=0.01, le=0.999)
    max_execution_price: float = Field(ge=0.01, le=1.0)
    trade_amount_usdc: float = Field(gt=0.0, le=100000.0)
    max_daily_loss: float = Field(ge=0.0)
    daily_profit_target: float = Field(ge=0.0)
    stale_data_max_age_ms: int = Field(ge=100, le=60000)
    duplicate_policy: str
    conflict_policy: str
    paper_slippage_bps: float = Field(ge=0.0, le=5000.0)
    paper_fee_rate: float = Field(ge=0.0, le=1.0)
    max_trades_per_day: int = Field(ge=1, le=10000)
    max_open_positions: int = Field(ge=0, le=100)
    min_price_gap_usd: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    paper_fill_ratio: float = Field(gt=0.0, le=1.0)
    paper_fee_exponent: float = Field(ge=0.0, le=5.0)
    paper_starting_balance: float = Field(gt=0.0)
    live_trading_enabled: bool
    live_credentials_present: bool
    geo_verified: bool

    @model_validator(mode="after")
    def _check_ranges(self) -> "RuntimeSettings":
        if self.duplicate_policy not in {"one_per_market", "one_per_side", "allow"}:
            raise ValueError("duplicate_policy must be one_per_market, one_per_side or allow")
        if self.conflict_policy not in {"up_first", "down_first", "highest_ask", "block_both"}:
            raise ValueError("conflict_policy must be up_first, down_first, highest_ask or block_both")
        if self.max_execution_price < self.trigger_threshold:
            raise ValueError("max_execution_price must be >= trigger_threshold")
        if self.max_execution_price < max(self.stage2_threshold, self.stage3_threshold):
            raise ValueError("max_execution_price must be >= the stage 2/3 thresholds")
        return self

    def to_public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data.pop("live_credentials_present", None)
        return data


class SettingsService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._overrides: dict[str, Any] = {}

    def load(self, session: Session) -> None:
        from app.database.models import BotSetting

        rows = session.execute(select(BotSetting)).scalars().all()
        self._overrides = {}
        for row in rows:
            if row.key in OVERRIDABLE_KEYS:
                self._overrides[row.key] = row.value

    def get(self) -> RuntimeSettings:
        merged: dict[str, Any] = {}
        for key in OVERRIDABLE_KEYS:
            merged[key] = getattr(self._settings, key)
        merged.update(self._overrides)
        merged.update(
            paper_fill_ratio=self._settings.paper_fill_ratio,
            paper_fee_exponent=self._settings.paper_fee_exponent,
            paper_starting_balance=self._settings.paper_starting_balance,
            live_trading_enabled=self._settings.live_trading_enabled,
            live_credentials_present=self._settings.live_credentials_present,
            geo_verified=self._settings.polymarket_geo_verified,
        )
        return RuntimeSettings(**merged)

    def update(self, session: Session, changes: dict[str, Any]) -> RuntimeSettings:
        from app.database.models import BotSetting

        clean = {k: v for k, v in changes.items() if k in OVERRIDABLE_KEYS and v is not None}
        if not clean:
            raise ValueError("no supported settings supplied")
        candidate = self.get().model_dump()
        candidate.update(clean)
        RuntimeSettings(**candidate)  # validate before persisting

        for key, value in clean.items():
            row = session.get(BotSetting, key)
            if row is None:
                session.add(BotSetting(key=key, value=value))
            else:
                row.value = value
            self._overrides[key] = value
        session.commit()
        return self.get()
