"""Per-stage WIN / LOSS count and PnL: counted only once a stage has been sold (take-profit, stop-loss, manual)."""

from __future__ import annotations

from itertools import count
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.models import Order
from app.database.session import session_scope
from app.main import create_app
from app.order_manager.manager import OrderManager
from app.services.order_outcomes import stage_stats

_ids = count(1)


def _order(market, key, side, size, price, fee=0.0):
    return SimpleNamespace(
        id=next(_ids), order_key=key, market_id=market, side=side,
        filled_size=size, avg_fill_price=price, fee=fee,
    )


def _stats(orders):
    return {s["stage"]: s for s in stage_stats(orders, OrderManager._key_stage, OrderManager._key_action)}


def test_each_stage_counts_its_own_wins_and_losses_after_the_exit():
    orders = [
        _order("m1", "paper:m1:UP:s0", "UP", 5.0, 0.70),
        _order("m1", "paper:m1:UP:s0:sell:aa", "UP", 5.0, 0.805),  # stage 0 take-profit: WIN
        _order("m1", "paper:m1", "UP", 5.0, 0.90),  # stage 1 (no suffix)
        _order("m1", "paper:m1:UP:s1:sell:bb", "UP", 5.0, 0.70),  # stage 1 stop-loss: LOSS
        _order("m1", "paper:m1:s2", "UP", 5.0, 0.92),  # stage 2 still held: not counted
    ]
    stats = _stats(orders)
    assert (stats[0]["wins"], stats[0]["losses"], stats[0]["trades"]) == (1, 0, 1)
    assert stats[0]["pnl"] > 0 and stats[0]["win_rate"] == 100.0
    assert (stats[1]["wins"], stats[1]["losses"]) == (0, 1) and stats[1]["pnl"] < 0
    assert (stats[2]["trades"], stats[2]["pnl"], stats[2]["win_rate"]) == (0, 0.0, None)
    assert stats[3]["trades"] == 0


def test_a_stage_across_markets_adds_up_and_partial_sells_are_one_trade():
    orders = [
        _order("m1", "paper:m1:UP:s0", "UP", 10.0, 0.60),
        _order("m1", "paper:m1:UP:s0:sell:a1", "UP", 4.0, 0.69),  # partial exit 1
        _order("m1", "paper:m1:UP:s0:sell:a2", "UP", 6.0, 0.69),  # partial exit 2: still ONE trade
        _order("m2", "paper:m2:DOWN:s0", "DOWN", 5.0, 0.80),
        _order("m2", "paper:m2:DOWN:s0:sell:b1", "DOWN", 5.0, 0.50),  # LOSS
    ]
    stats = _stats(orders)
    assert (stats[0]["trades"], stats[0]["wins"], stats[0]["losses"]) == (2, 1, 1)
    assert stats[0]["win_rate"] == 50.0
    assert abs(stats[0]["pnl"] - (10 * 0.09 + 5 * (0.50 - 0.80))) < 1e-6


def test_held_to_settlement_is_not_counted():
    orders = [_order("m1", "paper:m1:s3", "UP", 5.0, 0.93)]
    assert all(s["trades"] == 0 for s in _stats(orders).values())


async def test_manual_sell_counts_as_a_stage_exit_and_the_api_reports_it():
    from .helpers import books_for, build_stack, make_config, make_market
    from .test_stage0 import STAGE0, _stage0_buy

    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    books[market.up_token_id].bids = {0.70: 100.0}  # manual sell below the buy price -> LOSS
    sold = await stack["manager"].manual_sell(market, books, "paper", stack["config"], "UP", 0)
    assert sold.status == "FILLED"
    with session_scope() as session:
        orders = session.execute(select(Order).where(Order.filled_size > 0)).scalars().all()
    stats = _stats(orders)
    assert (stats[0]["wins"], stats[0]["losses"]) == (0, 1) and stats[0]["pnl"] < 0

    with TestClient(create_app()) as client:
        body = client.get("/api/stage-results?mode=paper").json()
    assert [s["stage"] for s in body["stages"]] == [0, 1, 2, 3]
    assert set(body["total"]) == {"wins", "losses", "trades", "win_rate", "pnl"}
