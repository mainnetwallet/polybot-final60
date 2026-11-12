from __future__ import annotations

from app.risk_manager.manager import RiskManager
from app.strategy.types import Signal

from .helpers import make_config, make_ctx


def signal(ask: float = 0.85, age_ms: float = 50, size: float | None = 100.0) -> Signal:
    return Signal(
        market_id="0xmarket",
        side="UP",
        token_id="up-token",
        best_ask=ask,
        ask_size=size,
        remaining_seconds=30.0,
        threshold=0.80,
        observed_at_ms=1,
        book_age_ms=age_ms,
    )


def test_risk_approves_valid_signal():
    decision = RiskManager().check(signal(), make_ctx(), make_config())
    assert decision.approved is True


def test_stale_data_blocks():
    decision = RiskManager().check(signal(age_ms=5000), make_ctx(), make_config())
    assert decision.approved is False
    assert decision.reason == "stale_data"


def test_expired_market_blocks():
    decision = RiskManager().check(
        signal(), make_ctx(remaining=0), make_config()
    )
    assert decision.reason == "market_expired"


def test_max_execution_price_blocks():
    decision = RiskManager().check(signal(ask=0.995), make_ctx(), make_config(max_execution_price=0.99))
    assert decision.approved is False
    assert decision.reason == "max_execution_price_exceeded"


def test_no_liquidity_blocks():
    decision = RiskManager().check(signal(size=0), make_ctx(), make_config())
    assert decision.reason == "no_liquidity"


def test_daily_loss_limit_blocks():
    decision = RiskManager().check(signal(), make_ctx(daily_pnl=-5.0), make_config())
    assert decision.reason == "daily_loss_limit"


def test_daily_profit_target_blocks():
    decision = RiskManager().check(signal(), make_ctx(daily_pnl=10.0), make_config())
    assert decision.reason == "daily_profit_target"


def test_insufficient_balance_blocks():
    decision = RiskManager().check(signal(), make_ctx(balance=2.0), make_config())
    assert decision.reason == "insufficient_balance"


def test_kill_switch_blocks():
    decision = RiskManager().check(signal(), make_ctx(kill_switch=True), make_config())
    assert decision.reason == "kill_switch"


def test_max_trades_per_day_blocks():
    decision = RiskManager().check(signal(), make_ctx(trades_today=60), make_config())
    assert decision.reason == "max_trades_per_day"
