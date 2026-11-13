from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StartRequest(BaseModel):
    confirm_live: bool = False
    confirmation_phrase: str = ""


class ModeRequest(BaseModel):
    mode: Literal["paper", "live"]
    confirm_live: bool = False
    confirmation_phrase: str = ""


class KillSwitchRequest(BaseModel):
    active: bool


class ResetPaperRequest(BaseModel):
    balance: float = Field(gt=0, le=100000)
    confirm: bool = False


class ResetDailyRequest(BaseModel):
    confirm: bool = False


class ResetServerRequest(BaseModel):
    confirm: bool = False
    confirmation_phrase: str = ""


class SettingsPatch(BaseModel):
    trigger_threshold: float | None = Field(default=None, ge=0.01, le=0.999)
    final_window_seconds: int | None = Field(default=None, ge=1, le=300)
    stage2_seconds: int | None = Field(default=None, ge=1, le=300)
    stage2_threshold: float | None = Field(default=None, ge=0.01, le=0.999)
    stage3_seconds: int | None = Field(default=None, ge=1, le=300)
    stage3_threshold: float | None = Field(default=None, ge=0.01, le=0.999)
    max_execution_price: float | None = Field(default=None, ge=0.01, le=1.0)
    trade_amount_usdc: float | None = Field(default=None, gt=0, le=100000)
    max_daily_loss: float | None = Field(default=None, ge=0)
    daily_profit_target: float | None = Field(default=None, ge=0)
    stale_data_max_age_ms: int | None = Field(default=None, ge=100, le=60000)
    duplicate_policy: Literal["one_per_market", "one_per_side", "allow"] | None = None
    conflict_policy: Literal["up_first", "down_first", "highest_ask", "block_both"] | None = None
    paper_slippage_bps: float | None = Field(default=None, ge=0, le=5000)
    paper_fee_rate: float | None = Field(default=None, ge=0, le=1)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=10000)
    max_open_positions: int | None = Field(default=None, ge=0, le=100)
    min_price_gap_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    stage_chain_enabled: bool | None = None

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}
