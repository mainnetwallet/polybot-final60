"""Stages trade and exit on their own: a stopped-out stage must not change another stage's result."""

from __future__ import annotations

from types import SimpleNamespace

from app.order_manager.manager import OrderManager
from app.services.order_outcomes import stage_outcomes

M = "m1"


def _order(key, side, size, price, fee=0.0):
    return SimpleNamespace(
        order_key=key, market_id=M, side=side, filled_size=size, avg_fill_price=price, fee=fee
    )


def _run(orders, position):
    return stage_outcomes(
        orders,
        {(M, "UP"): position},
        OrderManager._key_stage,
        OrderManager._key_action,
    )


def test_stopped_out_stage1_is_a_loss_while_stage2_and_3_stay_open():
    orders = [
        _order("paper:m1", "UP", 5.0, 0.90),  # stage 1 buy (no suffix)
        _order("paper:m1:s2", "UP", 5.0, 0.92),
        _order("paper:m1:s3", "UP", 5.0, 0.93),
        _order("paper:m1:UP:s1:sell:ab12cd34", "UP", 5.0, 0.70),  # stage 1 stop-loss
    ]
    out = _run(orders, SimpleNamespace(status="open", resolution=None))
    assert out[(M, "UP", 1)][0] == "LOSS" and out[(M, "UP", 1)][1] < 0
    assert out[(M, "UP", 2)] == ("OPEN", None)
    assert out[(M, "UP", 3)] == ("OPEN", None)


def test_settlement_pays_each_stage_by_its_own_cost():
    orders = [
        _order("paper:m1", "UP", 5.0, 0.90),
        _order("paper:m1:s3", "UP", 5.0, 0.93),
        _order("paper:m1:UP:s1:sell:ab12cd34", "UP", 5.0, 0.70),  # stage 1 stopped out earlier
    ]
    out = _run(orders, SimpleNamespace(status="closed", resolution="UP"))
    assert out[(M, "UP", 1)][0] == "LOSS"  # its own stop-loss, not rescued by the stage 3 payout
    assert out[(M, "UP", 3)][0] == "WIN"
    assert abs(out[(M, "UP", 3)][1] - 5.0 * (1.0 - 0.93)) < 1e-6

    lost = _run(orders, SimpleNamespace(status="closed", resolution="DOWN"))
    assert lost[(M, "UP", 3)][0] == "LOSS"
    assert abs(lost[(M, "UP", 3)][1] + 5.0 * 0.93) < 1e-6


def test_take_profit_win_of_one_stage_does_not_hide_a_losing_other_stage():
    orders = [
        _order("paper:m1", "UP", 5.0, 0.85),
        _order("paper:m1:s2", "UP", 5.0, 0.92),
        _order("paper:m1:UP:s1:sell:aaaaaaaa", "UP", 5.0, 0.95),  # stage 1 take-profit
        _order("paper:m1:UP:s2:sell:bbbbbbbb", "UP", 5.0, 0.60),  # stage 2 stop-loss
    ]
    out = _run(orders, SimpleNamespace(status="closed", resolution=None))
    assert out[(M, "UP", 1)][0] == "WIN" and out[(M, "UP", 1)][1] > 0
    assert out[(M, "UP", 2)][0] == "LOSS" and out[(M, "UP", 2)][1] < 0
