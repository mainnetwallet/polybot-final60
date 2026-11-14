"""Operator "sell now": sells what a stage still holds at the market, instantly and without conditions."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database.models import Order, Position
from app.database.session import session_scope

from .helpers import books_for, build_stack, make_config, make_market
from .test_stage0 import STAGE0, _event, _running_bot, _stage0_buy


async def test_manual_sell_sells_stage0_shares_at_the_bid_with_no_take_profit_or_stop_due():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    assert [r.status for r in await _stage0_buy(stack, market, books)] == ["FILLED"]
    manager = stack["manager"]
    books[market.up_token_id].bids = {0.78: 100.0}  # neither the take-profit (0.84) nor a stop is due
    assert await manager.handle_exits(market, books, "paper", stack["config"]) == []

    result = await manager.manual_sell(market, books, "paper", stack["config"], "UP", 0)
    assert result.status == "FILLED"
    with session_scope() as session:
        orders = session.execute(select(Order).order_by(Order.id)).scalars().all()
        position = session.execute(select(Position)).scalar_one()
    assert [manager._key_action(o.order_key) for o in orders] == ["BUY", "SELL"]
    assert orders[1].reason == "stage0_manual"
    assert orders[1].filled_size == pytest.approx(5.0)
    assert orders[1].avg_fill_price == pytest.approx(0.78)
    assert position.status == "closed" and position.size == pytest.approx(0.0)


async def test_manual_sell_twice_second_click_finds_nothing_to_sell():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    manager = stack["manager"]
    books[market.up_token_id].bids = {0.78: 100.0}
    first = await manager.manual_sell(market, books, "paper", stack["config"], "UP", 0)
    second = await manager.manual_sell(market, books, "paper", stack["config"], "UP", 0)
    assert first.status == "FILLED"
    assert (second.status, second.reason) == ("BLOCKED", "no_holding")
    with session_scope() as session:
        assert len(session.execute(select(Order)).scalars().all()) == 2  # one BUY, one SELL


async def test_manual_sell_blocked_without_shares_wrong_side_no_bid_or_ended_market():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    manager = stack["manager"]
    nothing = await manager.manual_sell(market, books, "paper", stack["config"], "UP", 0)
    assert (nothing.status, nothing.reason) == ("BLOCKED", "no_holding")

    await _stage0_buy(stack, market, books)
    wrong_side = await manager.manual_sell(market, books, "paper", stack["config"], "DOWN", 0)
    assert (wrong_side.status, wrong_side.reason) == ("BLOCKED", "no_holding")

    books[market.up_token_id].bids = {}
    no_bid = await manager.manual_sell(market, books, "paper", stack["config"], "UP", 0)
    assert (no_bid.status, no_bid.reason) == ("BLOCKED", "no_bid")

    ended = make_market(remaining=-1)
    over = await manager.manual_sell(ended, books, "paper", stack["config"], "UP", 0)
    assert (over.status, over.reason) == ("BLOCKED", "market_ended")
    assert (await manager.manual_sell(market, books, "paper", stack["config"], "UP", 7)).reason == "unknown_stage"


async def test_manual_sell_ignores_the_submission_gate_but_the_bot_must_be_running():
    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(100_150.0, 100_000.0, ""))
    books = books_for(market, up_ask=0.80, down_ask=0.40)
    bot.data._books = dict(books)
    await _event(bot, market)  # stage 0 buys UP
    books[market.up_token_id].bids = {0.78: 100.0}
    bot.data._books = dict(books)

    snapshot = await bot.snapshot()
    assert [(h["side"], h["stage"]) for h in snapshot["sellable"]] == [("UP", 0)]

    bot._risk_halt_reason = "daily_loss_limit"  # a risk halt must not trap the operator in a position
    sold = await bot.manual_sell(market.market_id, "UP", 0)
    assert sold["manual_sell"]["status"] == "FILLED"
    assert sold["sellable"] == []
    with session_scope() as session:
        assert session.execute(select(Position)).scalar_one().status == "closed"


async def test_bot_manual_sell_errors_when_stopped_or_market_ended():
    from app.services.bot import BotError

    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(100_150.0, 100_000.0, ""))
    with pytest.raises(BotError, match="running"):
        bot._status = "STOPPED"
        await bot.manual_sell(market.market_id, "UP", 0)
    bot._status = "RUNNING"
    with pytest.raises(BotError, match="ended"):
        await bot.manual_sell("some-other-market", "UP", 0)


async def test_manual_sell_sells_only_the_stage_that_was_clicked():
    from .test_stage0 import _stage_buy

    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0, paper_starting_balance=30.0))
    manager = stack["manager"]
    early = make_market(remaining=150)
    await _stage0_buy(stack, early, books_for(early, up_ask=0.80, down_ask=0.10))  # stage 0 holds UP
    late = make_market(remaining=55)
    books = books_for(late, up_ask=0.90, down_ask=0.05)
    assert [r.status for r in await _stage_buy(stack, late, books, 1)] == ["FILLED"]  # stage 1 holds UP too
    stage0_before = manager.load_stage0_holding("paper", late.market_id, 0).size
    stage1_before = manager.load_stage0_holding("paper", late.market_id, 1).size

    books[late.up_token_id].bids = {0.70: 1000.0}
    sold = await manager.manual_sell(late, books, "paper", stack["config"], "UP", 1)  # click stage 1
    assert sold.status == "FILLED" and sold.filled_size == pytest.approx(stage1_before, abs=0.01)
    assert manager.load_stage0_holding("paper", late.market_id, 1) is None
    still = manager.load_stage0_holding("paper", late.market_id, 0)  # stage 0 untouched
    assert still is not None and still.size == pytest.approx(stage0_before)
    with session_scope() as session:
        sells = [o for o in session.execute(select(Order)).scalars() if manager._key_action(o.order_key) == "SELL"]
        assert [(manager._key_stage(o.order_key), o.reason) for o in sells] == [(1, "stage1_manual")]
        assert session.execute(select(Position)).scalar_one().status == "open"

    sold0 = await manager.manual_sell(late, books, "paper", stack["config"], "UP", 0)  # now click stage 0
    assert sold0.status == "FILLED" and sold0.filled_size == pytest.approx(stage0_before)
