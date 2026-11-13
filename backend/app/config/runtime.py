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
    "stage2_min_price_gap_usd",
    "stage3_min_price_gap_usd",
    "stage0_enabled",
    "stage0_start_seconds",
    "stage0_min_ask",
    "stage0_max_ask",
    "stage0_min_price_gap_usd",
    "stage0_sell_price",
    "stage0_mid_from_ask",
    "stage0_high_from_ask",
    "stage0_sell_price_low",
    "stage0_sell_price_mid",
    "stage1_sell_enabled",
    "stage1_sell_max_buy",
    "stage1_sell_price",
    "stage1_sell_price_high",
    "stage2_sell_enabled",
    "stage2_sell_price",
    "stage0_stop_loss_pct",
    "stage1_stop_loss_pct",
    "stage2_stop_loss_pct",
    "stage3_stop_loss_pct",
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
    stage2_min_price_gap_usd: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    stage3_min_price_gap_usd: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    # Stage 0 (early entry + take-profit sell). Off by default when a RuntimeSettings is built
    # directly; the app's Settings (.env) turns it on.
    stage0_enabled: bool = False
    stage0_start_seconds: int = Field(default=200, ge=1, le=300)
    stage0_min_ask: float = Field(default=0.60, ge=0.01, le=0.999)
    stage0_max_ask: float = Field(default=0.95, ge=0.01, le=0.999)
    stage0_min_price_gap_usd: float = Field(default=50.0, ge=0.0, le=1_000_000.0)
    stage0_sell_price: float = Field(default=0.98, ge=0.02, le=0.999)
    stage0_mid_from_ask: float = Field(default=0.70, ge=0.01, le=0.999)
    stage0_high_from_ask: float = Field(default=0.80, ge=0.01, le=0.999)
    stage0_sell_price_low: float = Field(default=0.80, ge=0.02, le=0.999)
    stage0_sell_price_mid: float = Field(default=0.90, ge=0.02, le=0.999)
    stage1_sell_enabled: bool = False
    stage1_sell_max_buy: float = Field(default=0.90, ge=0.01, le=0.999)
    stage1_sell_price: float = Field(default=0.95, ge=0.02, le=0.999)
    stage1_sell_price_high: float = Field(default=0.99, ge=0.02, le=0.999)
    stage2_sell_enabled: bool = False
    stage2_sell_price: float = Field(default=0.99, ge=0.02, le=0.999)
    # Stop-loss in percent below the average buy price (0 = off). Off when a RuntimeSettings is built
    # directly; the app's Settings (.env) sets 20 for stage 0 and 15 for stages 1-3.
    stage0_stop_loss_pct: float = Field(default=0.0, ge=0.0, le=99.0)
    stage1_stop_loss_pct: float = Field(default=0.0, ge=0.0, le=99.0)
    stage2_stop_loss_pct: float = Field(default=0.0, ge=0.0, le=99.0)
    stage3_stop_loss_pct: float = Field(default=0.0, ge=0.0, le=99.0)
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
        if self.stage0_min_ask > self.stage0_max_ask:
            raise ValueError("stage0_min_ask must be <= stage0_max_ask")
        if self.stage0_sell_price <= self.stage0_max_ask:
            raise ValueError("stage0_sell_price must be greater than stage0_max_ask")
        if self.stage0_mid_from_ask > self.stage0_high_from_ask:
            raise ValueError("stage0_mid_from_ask must be <= stage0_high_from_ask")
        if self.stage0_sell_price_low <= min(self.stage0_mid_from_ask, self.stage0_max_ask):
            raise ValueError("stage0_sell_price_low must be above the buy prices of its tier")
        if self.stage0_sell_price_mid <= min(self.stage0_high_from_ask, self.stage0_max_ask):
            raise ValueError("stage0_sell_price_mid must be above the buy prices of its tier")
        if self.stage1_sell_price <= self.stage1_sell_max_buy:
            raise ValueError("stage1_sell_price must be greater than stage1_sell_max_buy")
        if self.stage1_sell_price_high < self.stage1_sell_price:
            raise ValueError("stage1_sell_price_high must be >= stage1_sell_price")
        if self.stage2_sell_price <= self.stage2_threshold:
            raise ValueError("stage2_sell_price must be greater than stage2_threshold")
        return self

    def stage1_sell_price_for(self, cost_per_share: float | None) -> float | None:
        """Take-profit price for stage 1 shares bought at `cost_per_share`; None = take-profit is off.

        Paid up to stage1_sell_max_buy (default 0.90) -> stage1_sell_price (0.95); paid more ->
        stage1_sell_price_high (0.99). Unknown cost uses the high price, so it never sells too early.
        """
        if not self.stage1_sell_enabled:
            return None
        if cost_per_share is None:
            return self.stage1_sell_price_high
        if cost_per_share <= self.stage1_sell_max_buy + 1e-6:
            return self.stage1_sell_price
        return self.stage1_sell_price_high

    def stage2_sell_price_for(self, cost_per_share: float | None) -> float | None:
        """Take-profit price for stage 2 shares (same price whatever they cost); None = off."""
        return self.stage2_sell_price if self.stage2_sell_enabled else None

    def stop_loss_pct_for(self, stage: int) -> float:
        """Stop-loss percentage configured for `stage` (0 = off)."""
        return {
            0: self.stage0_stop_loss_pct,
            1: self.stage1_stop_loss_pct,
            2: self.stage2_stop_loss_pct,
            3: self.stage3_stop_loss_pct,
        }.get(stage, 0.0)

    def stop_loss_price_for(self, stage: int, cost_per_share: float | None) -> float | None:
        """Best-bid level at or below which `stage`'s shares are sold; None = no stop-loss.

        cost * (1 - pct / 100) with the average price paid per share. An unknown cost has no
        reference price, so there is no stop for those shares.
        """
        pct = self.stop_loss_pct_for(stage)
        if pct <= 0 or cost_per_share is None or cost_per_share <= 0:
            return None
        return cost_per_share * (1.0 - pct / 100.0)

    def stage0_sell_price_for(self, cost_per_share: float | None) -> float:
        """Take-profit sell price for shares stage 0 bought at `cost_per_share` (unknown -> highest tier)."""
        if cost_per_share is None:
            return self.stage0_sell_price
        if cost_per_share + 1e-9 < self.stage0_mid_from_ask:
            return self.stage0_sell_price_low
        if cost_per_share + 1e-9 < self.stage0_high_from_ask:
            return self.stage0_sell_price_mid
        return self.stage0_sell_price

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
