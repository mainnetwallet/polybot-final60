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


def staged(stage: int, side: str = "UP") -> Signal:
    base = signal(ask=0.93)
    return Signal(**{**base.__dict__, "stage": stage, "side": side, "threshold": 0.90})


def test_add_on_stage_may_add_to_existing_position_even_at_position_cap():
    config = make_config(max_open_positions=1)
    ctx = make_ctx(open_positions=1, exposed_sides=frozenset({"UP"}), stage1_bought=True, stage1_remaining=60)
    assert RiskManager().check(staged(2), ctx, config).approved is True
    assert RiskManager().check(staged(3), ctx, config).approved is True


def test_stage1_and_opposite_side_still_respect_position_cap():
    config = make_config(max_open_positions=1)
    ctx = make_ctx(open_positions=1, exposed_sides=frozenset({"UP"}), stage1_bought=True, stage1_remaining=60)
    assert RiskManager().check(staged(1), ctx, config).reason == "max_open_positions"
    assert RiskManager().check(staged(2, side="DOWN"), ctx, config).reason == "max_open_positions"


# ---------------------------------------------------------------------------
# Price-to-beat gap rule
# ---------------------------------------------------------------------------
def _side(side: str, stage: int = 1, ask: float = 0.85) -> Signal:
    base = signal(ask=ask)
    return Signal(**{**base.__dict__, "side": side, "stage": stage})


def _gap_config(gap: float = 20.0, **overrides):
    overrides.setdefault("stage2_min_price_gap_usd", 25.0)
    overrides.setdefault("stage3_min_price_gap_usd", 30.0)
    return make_config(min_price_gap_usd=gap, **overrides)


def test_gap_rule_is_off_by_default_and_ignores_missing_price_data():
    config = make_config()
    assert config.min_price_gap_usd == 0
    assert RiskManager().check(signal(), make_ctx(), config).approved is True


def test_up_needs_price_at_least_gap_above_price_to_beat():
    ctx = lambda price: make_ctx(btc_price=price, price_to_beat=100_000.0)  # noqa: E731
    risk = RiskManager()
    assert risk.check(_side("UP"), ctx(100_020.0), _gap_config()).approved is True  # exactly the gap
    assert risk.check(_side("UP"), ctx(100_150.0), _gap_config()).approved is True
    too_small = risk.check(_side("UP"), ctx(100_019.99), _gap_config())
    assert too_small.reason == "price_gap_too_small"
    assert risk.check(_side("UP"), ctx(100_000.0), _gap_config()).reason == "price_gap_too_small"
    # price on the wrong side of the price to beat
    assert risk.check(_side("UP"), ctx(99_900.0), _gap_config()).reason == "price_gap_too_small"


def test_down_needs_price_at_least_gap_below_price_to_beat():
    ctx = lambda price: make_ctx(btc_price=price, price_to_beat=100_000.0)  # noqa: E731
    risk = RiskManager()
    assert risk.check(_side("DOWN"), ctx(99_980.0), _gap_config()).approved is True  # exactly the gap
    assert risk.check(_side("DOWN"), ctx(99_800.0), _gap_config()).approved is True
    assert risk.check(_side("DOWN"), ctx(99_980.01), _gap_config()).reason == "price_gap_too_small"
    assert risk.check(_side("DOWN"), ctx(100_100.0), _gap_config()).reason == "price_gap_too_small"


def test_gap_rule_applies_to_add_on_stages_too():
    config = _gap_config(max_open_positions=1)
    exposed = frozenset({"UP"})
    weak = make_ctx(
        open_positions=1, exposed_sides=exposed, btc_price=100_010.0, price_to_beat=100_000.0,
        stage1_bought=True, stage1_remaining=60,
    )
    strong = make_ctx(
        open_positions=1, exposed_sides=exposed, btc_price=100_030.0, price_to_beat=100_000.0,
        stage1_bought=True, stage1_remaining=60,
    )
    for stage in (2, 3):
        assert RiskManager().check(_side("UP", stage=stage, ask=0.93), weak, config).reason == "price_gap_too_small"
        assert RiskManager().check(_side("UP", stage=stage, ask=0.93), strong, config).approved is True


def test_each_stage_has_its_own_gap_20_25_30():
    config = _gap_config(max_open_positions=1)
    exposed = frozenset({"UP"})

    def ctx_for(stage: int, move: float):
        # stage 1 is the first buy (no position yet); stage 2/3 add to the held UP position
        return make_ctx(
            open_positions=0 if stage == 1 else 1,
            exposed_sides=frozenset() if stage == 1 else exposed,
            btc_price=100_000.0 + move, price_to_beat=100_000.0,
            stage1_bought=stage != 1, stage1_remaining=None if stage == 1 else 60,
        )

    def verdict(stage: int, move: float) -> str:
        return RiskManager().check(_side("UP", stage=stage, ask=0.93), ctx_for(stage, move), config).reason

    assert verdict(1, 20.0) == "approved"
    assert verdict(1, 19.99) == "price_gap_too_small"
    assert verdict(2, 24.99) == "price_gap_too_small"
    assert verdict(2, 25.0) == "approved"
    assert verdict(3, 29.99) == "price_gap_too_small"
    assert verdict(3, 30.0) == "approved"


def test_gap_rule_fails_closed_without_price_data():
    risk = RiskManager()
    no_price = risk.check(_side("UP"), make_ctx(price_gap_issue="BTC price is stale (12s old)"), _gap_config())
    assert no_price.approved is False
    assert no_price.reason == "price_gap_unavailable"
    assert "stale" in no_price.detail
    no_beat = risk.check(_side("UP"), make_ctx(btc_price=100_050.0), _gap_config())
    assert no_beat.reason == "price_gap_unavailable"


def test_gap_threshold_is_configurable():
    ctx = make_ctx(btc_price=100_005.0, price_to_beat=100_000.0)
    assert RiskManager().check(_side("UP"), ctx, _gap_config(gap=5.0)).approved is True
    assert RiskManager().check(_side("UP"), ctx, _gap_config(gap=5.01)).reason == "price_gap_too_small"


# ---------------------------------------------------------------------------
# Stage chain
# ---------------------------------------------------------------------------
def _chain_config(**overrides):
    return make_config(max_open_positions=1, **overrides)


def _chain_ctx(remaining, bought=False, s1_remaining=None):
    return make_ctx(
        remaining=remaining,
        open_positions=1 if bought else 0,
        exposed_sides=frozenset({"UP"}) if bought else frozenset(),
        stage1_bought=bought,
        stage1_remaining=s1_remaining,
    )


def test_stage1_can_buy_until_the_last_30_seconds_then_only_stage1_stays_off():
    risk, config = RiskManager(), _chain_config()
    assert risk.check(_side("UP"), _chain_ctx(55), config).approved is True
    assert risk.check(_side("UP"), _chain_ctx(35), config).approved is True  # late, still stage 1
    assert risk.check(_side("UP"), _chain_ctx(30), config).reason == "stage1_missed"
    # stages 2 and 3 do not need stage 1: their own rules decide
    assert risk.check(_side("UP", stage=2, ask=0.93), _chain_ctx(25), config).approved is True
    assert risk.check(_side("UP", stage=3, ask=0.93), _chain_ctx(25), config).approved is True


def test_stage2_does_not_need_stage1():
    risk, config = RiskManager(), _chain_config()
    s2 = _side("UP", stage=2, ask=0.93)
    assert risk.check(s2, _chain_ctx(45), config).approved is True  # stage 1 never bought
    assert risk.check(s2, _chain_ctx(45, bought=True, s1_remaining=62), config).approved is True
    assert risk.check(s2, _chain_ctx(35, bought=True, s1_remaining=40), config).approved is True
    assert risk.check(s2, _chain_ctx(45, bought=True, s1_remaining=None), config).approved is True


def test_stage3_is_independent_of_stage1_and_stage2():
    risk, config = RiskManager(), _chain_config()
    s3 = _side("UP", stage=3, ask=0.93)
    assert risk.check(s3, _chain_ctx(25), config).approved is True  # stage 1 never bought
    assert risk.check(s3, _chain_ctx(25, bought=True, s1_remaining=70), config).approved is True
    assert risk.check(s3, _chain_ctx(25, bought=True, s1_remaining=40), config).approved is True  # stage 2 skipped
    assert risk.check(s3, _chain_ctx(25, bought=True, s1_remaining=None), config).approved is True
