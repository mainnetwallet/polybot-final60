"""Stage 0: early entry (200s -> final window, ask <= 0.95, BTC gap >= $100) + take-profit sell at 0.98."""

from __future__ import annotations

import time

from sqlalchemy import select

from app.database.models import Order, Position
from app.database.session import session_scope
from app.execution.base import FillPart, ExecutionReport, SellRequest
from app.paper_trading.engine import PaperExecutionEngine
from app.risk_manager.manager import RiskManager
from app.strategy.engine import FinalWindowStrategy, datetime_to_epoch_ms

from .helpers import (
    books_for,
    build_stack,
    make_book,
    make_config,
    make_ctx,
    make_market,
    stage_books,
)

STAGE0 = dict(stage0_enabled=True)  # window 200s -> 60s (the test final window), ask <= .95, gap $100, sell .98


def evaluate(remaining: float, up: float, down: float, **config):
    market = make_market(remaining=remaining + 1000)
    now_ms = datetime_to_epoch_ms(market.window_end) - int(remaining * 1000)
    books = books_for(market, up, down)
    return FinalWindowStrategy().evaluate(market, books, now_ms, make_config(**{**STAGE0, **config}))


# ---------------------------------------------------------------- strategy
def test_stage0_signals_between_200s_and_final_window():
    result = evaluate(150, up=0.90, down=0.10)
    assert result.in_window is True
    assert {(s.stage, s.side) for s in result.signals} == {(0, "UP"), (0, "DOWN")}  # both asks <= .95


def test_stage0_ask_above_limit_is_not_a_signal():
    result = evaluate(150, up=0.96, down=0.04)
    assert [(s.stage, s.side) for s in result.signals] == [(0, "DOWN")]
    assert result.signals[0].threshold == 0.95


def test_stage0_off_before_200s_and_inside_final_window():
    assert evaluate(230, 0.9, 0.1).reason == "outside_final_window"
    assert evaluate(230, 0.9, 0.1).signals == []
    inside = evaluate(55, 0.9, 0.1)  # final window: stage 0 is over, stage 1 rules apply
    assert all(s.stage != 0 for s in inside.signals)
    boundary = evaluate(60.5, 0.9, 0.1)  # just before the final window opens
    assert any(s.stage == 0 for s in boundary.signals)


def test_stage0_disabled_changes_nothing():
    result = evaluate(150, 0.9, 0.1, stage0_enabled=False)
    assert result.in_window is False and result.signals == []


# ---------------------------------------------------------------- risk
def _fresh(signal):
    from dataclasses import replace

    return replace(signal, book_age_ms=10.0)  # the helper's fake clock would make the book look stale


def _signal(remaining=150, up=0.90, down=0.10, side="UP"):
    result = evaluate(remaining, up, down)
    return _fresh(next(s for s in result.signals if s.side == side))


def test_risk_approves_stage0_with_gap():
    config = make_config(**STAGE0)
    ctx = make_ctx(remaining=150, btc_price=100_150.0, price_to_beat=100_000.0)
    assert RiskManager().check(_signal(), ctx, config).approved


def test_risk_blocks_small_gap_and_wrong_direction():
    config = make_config(**STAGE0)
    small = make_ctx(remaining=150, btc_price=100_050.0, price_to_beat=100_000.0)
    assert RiskManager().check(_signal(), small, config).reason == "price_gap_too_small"
    wrong = make_ctx(remaining=150, btc_price=99_850.0, price_to_beat=100_000.0)
    assert RiskManager().check(_signal(), wrong, config).reason == "price_gap_too_small"
    down = _signal(up=0.10, down=0.90, side="DOWN")
    assert RiskManager().check(down, wrong, config).approved


def test_risk_blocks_without_btc_data_ask_and_window():
    config = make_config(**STAGE0)
    ctx = make_ctx(remaining=150)
    assert RiskManager().check(_signal(), ctx, config).reason == "price_gap_unavailable"
    good = make_ctx(remaining=150, btc_price=100_150.0, price_to_beat=100_000.0)
    expensive = _signal(up=0.95)
    from dataclasses import replace

    assert RiskManager().check(replace(expensive, best_ask=0.96), good, config).reason == "stage0_ask_too_high"
    late = make_ctx(remaining=55, btc_price=100_150.0, price_to_beat=100_000.0)
    assert RiskManager().check(_signal(), late, config).reason == "outside_stage0_window"
    off = make_config(stage0_enabled=False)
    assert RiskManager().check(_signal(), good, off).reason == "stage0_disabled"


def test_stage0_holding_does_not_block_stage1_open_position_cap():
    config = make_config(**STAGE0, max_open_positions=1)
    stage1 = _fresh(next(s for s in evaluate(45, 0.90, 0.10).signals if s.stage == 1))
    blocked = make_ctx(remaining=45, open_positions=1, exposed_sides=frozenset({"UP"}))
    assert RiskManager().check(stage1, blocked, config).reason == "max_open_positions"
    free = make_ctx(
        remaining=45, open_positions=1, exposed_sides=frozenset({"UP"}), stage0_pure_sides=frozenset({"UP"})
    )
    assert RiskManager().check(stage1, free, config).reason != "max_open_positions"


# ---------------------------------------------------------------- paper sell
def _paper(books):
    from app.config.settings import get_settings

    return PaperExecutionEngine(get_settings(), lambda side: books.get(side))


def _sell(size=2.0, min_price=0.98):
    return SellRequest(
        order_key="k", client_order_id="c", market_id="m", side="UP", token_id="t",
        size=size, min_price=min_price, observed_at_ms=int(time.time() * 1000),
    )


async def test_paper_sell_fills_at_or_above_floor():
    book = make_book("t", "UP", [(0.99, 10)], bids=[(0.98, 5.0), (0.97, 50.0)])
    report = await _paper({"UP": book}).place_sell(_sell(size=2.0))
    assert report.status == "FILLED"
    assert report.filled_size == 2.0 and report.avg_price == 0.98
    assert report.fee > 0


async def test_paper_sell_never_goes_below_floor_and_is_partial_when_thin():
    book = make_book("t", "UP", [(0.99, 10)], bids=[(0.98, 1.0), (0.97, 50.0)])
    report = await _paper({"UP": book}).place_sell(_sell(size=2.0))
    assert report.filled_size == 1.0  # the 0.97 level is below the floor
    assert report.status == "CANCELED"  # IOC: the rest is not working on the book
    low = make_book("t", "UP", [(0.99, 10)], bids=[(0.97, 50.0)])
    none = await _paper({"UP": low}).place_sell(_sell())
    assert none.status == "CANCELED" and none.filled_size == 0


# ---------------------------------------------------------------- portfolio
def test_sell_report_books_pnl_and_balance():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        order = Order(
            order_key="paper:m:s0", client_order_id="a", mode="paper", market_id="0xmarket", side="UP",
            token_id="up-token", status="FILLED", requested_notional=1.0, requested_price=0.9,
        )
        session.add(order)
        session.flush()
        buy = ExecutionReport(
            status="FILLED", filled_size=10.0, avg_price=0.90, fee=0.05,
            fills=[FillPart(price=0.90, size=10.0, fee=0.05)],
        )
        portfolio.apply_report(session, "paper", order, buy)
        before = portfolio.balance(session, "paper")
        sell_order = Order(
            order_key="paper:m:up:s0:sell:1", client_order_id="b", mode="paper", market_id="0xmarket",
            side="UP", token_id="up-token", status="FILLED", requested_notional=1.0, requested_price=0.98,
        )
        session.add(sell_order)
        session.flush()
        sale = ExecutionReport(
            status="FILLED", filled_size=10.0, avg_price=0.98, fee=0.02,
            fills=[FillPart(price=0.98, size=10.0, fee=0.02)],
        )
        pnl = portfolio.apply_sell_report(session, "paper", sell_order, sale)
        after = portfolio.balance(session, "paper")
        position = session.execute(select(Position)).scalar_one()
    assert round(pnl, 6) == round(9.8 - 0.02 - 9.0 - 0.05, 6)
    assert round(after - before, 6) == round(9.8 - 0.02, 6)
    assert position.status == "closed" and position.size == 0 and position.resolution is None
    assert round(position.realized_pnl, 6) == round(pnl, 6)


# ---------------------------------------------------------------- order flow
async def _stage0_buy(stack, market, books):
    stage_books(stack, market, books)
    now_ms = int(time.time() * 1000)
    evaluation = stack["strategy"].evaluate(market, books, now_ms, stack["config"])
    signals = [s for s in evaluation.signals if s.stage == 0 and s.side == "UP"]
    evaluation.signals = signals
    ctx = make_ctx(remaining=market.remaining_seconds(), btc_price=100_150.0, price_to_beat=100_000.0)
    return await stack["manager"].handle_event(market, evaluation, books, "paper", ctx)


async def test_stage0_buy_then_sell_at_98_closes_position():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.90, down_ask=0.10)

    results = await _stage0_buy(stack, market, books)
    assert [r.status for r in results] == ["FILLED"]
    with session_scope() as session:
        order = session.execute(select(Order)).scalar_one()
    assert order.order_key.endswith(":s0") and order.requested_price == 0.90
    assert stack["manager"]._key_stage(order.order_key) == 0

    # bid still below 0.98 -> nothing is sold
    books[market.up_token_id].bids = {0.97: 100.0}
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []

    # bid reaches 0.98 -> everything stage 0 holds is sold
    books[market.up_token_id].bids = {0.98: 100.0}
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [r.status for r in results] == ["FILLED"]
    with session_scope() as session:
        orders = session.execute(select(Order).order_by(Order.id)).scalars().all()
        position = session.execute(select(Position)).scalar_one()
    assert [stack["manager"]._key_action(o.order_key) for o in orders] == ["BUY", "SELL"]
    assert abs(orders[1].filled_size - 4.44) < 1e-6  # 4.444 shares held, sold in whole cents
    assert position.status == "closed" and position.realized_pnl > 0
    # sold shares are gone: another exit pass does nothing
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []


async def test_stage0_sell_is_never_rebought_and_does_not_count_as_stage1():
    stack = build_stack(make_config(**STAGE0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.90, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    books[market.up_token_id].bids = {0.98: 0.5}  # thin: only part of the shares can be sold
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert results and results[0].status == "CANCELED" and results[0].filled_size > 0
    manager = stack["manager"]
    candidates = manager._partial_order_candidates(market.market_id, "paper")
    assert all("sell" not in key for *_rest, key in candidates)
    assert manager.stage1_remaining("paper", market.market_id) is None
    # remaining shares are still held and can be sold on the next try
    holding = manager.load_stage0_holding("paper", market.market_id)
    assert holding is not None and holding.size > 0


async def test_stage0_buy_never_pays_above_its_limit():
    stack = build_stack(make_config(**STAGE0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.96, down_ask=0.04)  # UP is above the .95 limit
    stage_books(stack, market, books)
    now_ms = int(time.time() * 1000)
    evaluation = stack["strategy"].evaluate(market, books, now_ms, stack["config"])
    assert all(not (s.stage == 0 and s.side == "UP") for s in evaluation.signals)


# ---------------------------------------------------------------- whole bot
def _running_bot(market, btc):
    from app.config.runtime import SettingsService
    from app.config.settings import get_settings
    from app.monitoring.logs import NullPublisher
    from app.portfolio.analytics import AnalyticsService
    from app.portfolio.service import Portfolio
    from app.services.bot import TradingBot

    settings = get_settings()
    portfolio = Portfolio(settings)
    bot = TradingBot(settings, SettingsService(settings), portfolio, AnalyticsService(), NullPublisher())
    with session_scope() as session:
        portfolio.ensure_accounts(session)
        account = portfolio.account(session, "paper")
        account.balance = account.starting_balance = 10.0
    bot._market = market
    bot._status = "RUNNING"
    bot.data.set_market(market)
    bot.btc.gap_inputs = lambda _market: btc
    return bot


async def _event(bot, market):
    config = bot.runtime.get()
    now_ms = int(time.time() * 1000)
    evaluation = bot.strategy.evaluate(market, bot.data.books, now_ms, config)
    await bot._handle_trading(market, evaluation, config, now_ms)


async def test_bot_buys_stage0_on_gap_side_only_then_sells_at_98():
    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(99_850.0, 100_000.0, ""))  # BTC is $150 BELOW the price to beat
    books = books_for(market, up_ask=0.40, down_ask=0.88)  # both asks <= .95
    bot.data._books = dict(books)

    await _event(bot, market)
    with session_scope() as session:
        orders = session.execute(select(Order)).scalars().all()
    assert [(o.side, o.status) for o in orders] == [("DOWN", "FILLED")]  # UP has no gap -> never tried
    assert orders[0].requested_price == 0.88

    await _event(bot, market)  # nothing more: stage 0 buys once per market
    with session_scope() as session:
        assert len(session.execute(select(Order)).scalars().all()) == 1

    books[market.down_token_id].bids = {0.98: 100.0}
    await _event(bot, market)
    rows = await bot.recent_orders("paper")
    assert sorted((r["action"], r["stage"], r["status"]) for r in rows) == [
        ("BUY", 0, "FILLED"), ("SELL", 0, "FILLED"),
    ]
    buy = next(r for r in rows if r["action"] == "BUY")
    assert buy["result"] == "WIN" and buy["result_pnl"] > 0
    snap = await bot.snapshot()
    assert snap["positions"] == []  # closed by the sale, nothing waits for settlement
    assert snap["account"]["balance"] > 10.0 - 1e-9
    assert snap["strategy"]["stage0_sell_price"] == 0.98


async def test_bot_stage0_needs_gap_and_stops_in_final_window():
    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(100_050.0, 100_000.0, ""))  # only $50 beyond: below the $100 gap
    bot.data._books = dict(books_for(market, up_ask=0.90, down_ask=0.10))
    await _event(bot, market)
    with session_scope() as session:
        assert session.execute(select(Order)).scalars().all() == []

    late = make_market(remaining=55)  # final window: stage 0 is over
    bot2 = _running_bot(late, btc=(100_300.0, 100_000.0, ""))
    bot2.data._books = dict(books_for(late, up_ask=0.90, down_ask=0.10))
    await _event(bot2, late)
    with session_scope() as session:
        stages = [bot2.orders._key_stage(o.order_key) for o in session.execute(select(Order)).scalars()]
    assert 0 not in stages
