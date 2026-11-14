"""Stage 0: early entry (200s -> 100s, ask .60-.85, BTC gap >= $50) + take-profit sell (+15 / +10 / +5 % by buy price)."""

from __future__ import annotations

import time

from sqlalchemy import select

from app.config.settings import Settings
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

STAGE0 = dict(stage0_enabled=True)  # window 200s -> 60s (the test final window), ask .60-.85, gap $80, take-profit +15 / +10 / +5 %


def evaluate(remaining: float, up: float, down: float, **config):
    market = make_market(remaining=remaining + 1000)
    now_ms = datetime_to_epoch_ms(market.window_end) - int(remaining * 1000)
    books = books_for(market, up, down)
    return FinalWindowStrategy().evaluate(market, books, now_ms, make_config(**{**STAGE0, **config}))


# ---------------------------------------------------------------- strategy
def test_stage0_signals_between_200s_and_final_window():
    result = evaluate(150, up=0.80, down=0.10)
    assert result.in_window is True
    # UP ask .80 is inside .60-.85; DOWN ask .10 is below the .60 minimum
    assert {(s.stage, s.side) for s in result.signals} == {(0, "UP")}
    both = evaluate(150, up=0.65, down=0.62)
    assert {(s.stage, s.side) for s in both.signals} == {(0, "UP"), (0, "DOWN")}


def test_stage0_ask_outside_60_85_is_not_a_signal():
    above = evaluate(150, up=0.86, down=0.14)
    assert above.signals == []  # .86 is above the .85 limit, .14 below the .60 minimum
    assert evaluate(150, up=0.90, down=0.10).signals == []  # the old .95 range no longer buys
    assert evaluate(150, up=0.85, down=0.15).signals[0].side == "UP"  # .85 itself is allowed
    assert evaluate(150, up=0.60, down=0.40).signals[0].side == "UP"  # .60 itself is allowed
    assert evaluate(150, up=0.59, down=0.41).signals == []  # below the minimum
    result = evaluate(150, up=0.70, down=0.30)
    assert result.signals[0].threshold == 0.85


def test_stage0_window_is_200s_to_100s_independent_of_final_window():
    assert evaluate(230, 0.9, 0.1).reason == "outside_final_window"
    assert evaluate(230, 0.9, 0.1).signals == []
    start = evaluate(200, 0.8, 0.2)  # 200s left: stage 0 opens
    assert any(s.stage == 0 for s in start.signals)
    boundary = evaluate(100.5, 0.8, 0.2)  # just before stage 0 ends
    assert any(s.stage == 0 for s in boundary.signals)
    ended = evaluate(100, 0.8, 0.2)  # 100s left: stage 0 is over
    assert all(s.stage != 0 for s in ended.signals)
    gap = evaluate(80, 0.8, 0.2, final_window_seconds=60)  # between stage 0 end and the final window: nothing
    assert all(s.stage != 0 for s in gap.signals)
    # moving the final window does not move the stage 0 end
    wide = evaluate(90, 0.8, 0.2, final_window_seconds=120)
    assert all(s.stage != 0 for s in wide.signals)


def test_stage0_end_can_be_changed():
    result = evaluate(70, 0.8, 0.2, stage0_end_seconds=50)
    assert any(s.stage == 0 for s in result.signals)


def test_stage0_disabled_changes_nothing():
    result = evaluate(150, 0.9, 0.1, stage0_enabled=False)
    assert result.in_window is False and result.signals == []


# ---------------------------------------------------------------- risk
def _fresh(signal):
    from dataclasses import replace

    return replace(signal, book_age_ms=10.0)  # the helper's fake clock would make the book look stale


def _signal(remaining=150, up=0.80, down=0.10, side="UP"):
    result = evaluate(remaining, up, down)
    return _fresh(next(s for s in result.signals if s.side == side))


def test_risk_approves_stage0_with_gap():
    config = make_config(**STAGE0)
    ctx = make_ctx(remaining=150, btc_price=100_150.0, price_to_beat=100_000.0)
    assert RiskManager().check(_signal(), ctx, config).approved


def test_risk_blocks_small_gap_and_wrong_direction():
    config = make_config(**STAGE0)
    small = make_ctx(remaining=150, btc_price=100_030.0, price_to_beat=100_000.0)
    assert RiskManager().check(_signal(), small, config).reason == "price_gap_too_small"
    wrong = make_ctx(remaining=150, btc_price=99_850.0, price_to_beat=100_000.0)
    assert RiskManager().check(_signal(), wrong, config).reason == "price_gap_too_small"
    down = _signal(up=0.10, down=0.80, side="DOWN")
    assert RiskManager().check(down, wrong, config).approved


def test_risk_blocks_without_btc_data_ask_and_window():
    config = make_config(**STAGE0)
    ctx = make_ctx(remaining=150)
    assert RiskManager().check(_signal(), ctx, config).reason == "price_gap_unavailable"
    good = make_ctx(remaining=150, btc_price=100_150.0, price_to_beat=100_000.0)
    expensive = _signal(up=0.85)
    from dataclasses import replace

    assert RiskManager().check(replace(expensive, best_ask=0.86), good, config).reason == "stage0_ask_too_high"
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


async def test_stage0_buy_then_sell_at_plus_5_percent_closes_position():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)

    results = await _stage0_buy(stack, market, books)
    assert [r.status for r in results] == ["FILLED"]
    with session_scope() as session:
        order = session.execute(select(Order)).scalar_one()
    assert order.order_key.endswith(":s0") and order.requested_price == 0.80
    assert stack["manager"]._key_stage(order.order_key) == 0

    # bought at 0.80 (high tier, +5 %): the take-profit price is 0.84. Bid 0.83 -> nothing is sold
    books[market.up_token_id].bids = {0.83: 100.0}
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []

    # bid reaches 0.84 -> everything stage 0 holds is sold
    books[market.up_token_id].bids = {0.84: 100.0}
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [r.status for r in results] == ["FILLED"]
    with session_scope() as session:
        orders = session.execute(select(Order).order_by(Order.id)).scalars().all()
        position = session.execute(select(Position)).scalar_one()
    assert [stack["manager"]._key_action(o.order_key) for o in orders] == ["BUY", "SELL"]
    assert abs(orders[1].filled_size - 5.0) < 1e-6  # 4 USDC at 0.80 = 5 shares
    assert position.status == "closed" and position.realized_pnl > 0
    # sold shares are gone: another exit pass does nothing
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []


async def test_stage0_sell_is_never_rebought_and_does_not_count_as_stage1():
    stack = build_stack(make_config(**STAGE0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    books[market.up_token_id].bids = {0.84: 0.5}  # thin: only part of the shares can be sold
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
    books = books_for(market, up_ask=0.86, down_ask=0.14)  # UP is above the .85 limit
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


async def test_bot_buys_stage0_on_gap_side_only_then_sells_at_plus_5_percent():
    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(99_850.0, 100_000.0, ""))  # BTC is $150 BELOW the price to beat
    books = books_for(market, up_ask=0.40, down_ask=0.80)  # both asks <= .85
    bot.data._books = dict(books)

    await _event(bot, market)
    with session_scope() as session:
        orders = session.execute(select(Order)).scalars().all()
    assert [(o.side, o.status) for o in orders] == [("DOWN", "FILLED")]  # UP has no gap -> never tried
    assert orders[0].requested_price == 0.80

    await _event(bot, market)  # nothing more: stage 0 buys once per market
    with session_scope() as session:
        assert len(session.execute(select(Order)).scalars().all()) == 1

    books[market.down_token_id].bids = {0.84: 100.0}
    await _event(bot, market)
    rows = await bot.recent_orders("paper")
    assert sorted((r["action"], r["stage"], r["status"]) for r in rows) == [
        ("BUY", 0, "FILLED"), ("SELL", 0, "FILLED"),
    ]
    buy = next(r for r in rows if r["action"] == "BUY")
    sell = next(r for r in rows if r["action"] == "SELL")
    assert buy["result"] is None and buy["result_pnl"] is None  # a BUY row stays blank
    assert sell["result"] == "WIN" and sell["result_pnl"] > 0  # the exit carries the result
    snap = await bot.snapshot()
    assert snap["positions"] == []  # closed by the sale, nothing waits for settlement
    assert snap["account"]["balance"] > 10.0 - 1e-9
    strategy = snap["strategy"]
    assert (strategy["stage0_take_profit_pct_low"], strategy["stage0_take_profit_pct_mid"], strategy["stage0_take_profit_pct_high"]) == (15.0, 10.0, 5.0)
    assert strategy["stage0_max_ask"] == 0.85


async def test_bot_stage0_needs_gap_and_stops_in_final_window():
    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(100_030.0, 100_000.0, ""))  # only $30 beyond: below the $50 gap
    bot.data._books = dict(books_for(market, up_ask=0.80, down_ask=0.10))
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


async def test_stage0_sell_orders_do_not_count_as_daily_trades():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    books[market.up_token_id].bids = {0.84: 100.0}
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [r.status for r in results] == ["FILLED"]
    with session_scope() as session:
        assert session.query(Order).count() == 2  # one BUY + one SELL row
        assert stack["portfolio"].trades_today(session, "paper") == 1  # only the entry counts


async def test_stage0_retry_loop_stays_awake_right_after_the_buy():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    # the holding registry is not loaded yet, but the bought marker keeps the exit watch active
    assert stack["manager"].has_stage0_holding("paper", market.market_id) is True
    assert stack["manager"].has_stage0_holding("paper", "some-other-market") is False


async def test_stage0_sell_only_sells_stage0_shares_not_stage1():
    """Stage 0 bought UP, then stage 1 bought UP in the final window: the +5 % sell takes ONLY stage 0's shares."""
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    with session_scope() as session:
        s0_shares = session.execute(select(Order)).scalar_one().filled_size

    # final window: stage 1 buys UP at 0.85 on top of the stage 0 holding
    late = make_market(remaining=45)
    books1 = books_for(late, up_ask=0.85, down_ask=0.15)
    stage_books(stack, late, books1)
    evaluation = stack["strategy"].evaluate(late, books1, int(time.time() * 1000), stack["config"])
    evaluation.signals = [s for s in evaluation.signals if s.stage == 1 and s.side == "UP"]
    ctx = make_ctx(remaining=45, btc_price=100_150.0, price_to_beat=100_000.0)
    results = await stack["manager"].handle_event(late, evaluation, books1, "paper", ctx)
    assert [r.status for r in results] == ["FILLED"]
    with session_scope() as session:
        s1_order = session.execute(select(Order).order_by(Order.id.desc())).scalars().first()
        s1_shares = s1_order.filled_size
        position = session.execute(select(Position)).scalar_one()
        assert abs(position.size - (s0_shares + s1_shares)) < 1e-6

    # bid reaches 0.84: only the stage 0 shares (whole cents) are sold, stage 1 shares stay held
    books1[late.up_token_id].bids = {0.84: 100.0}
    results = await stack["manager"].handle_exits(late, books1, "paper", stack["config"])
    assert [r.status for r in results] == ["FILLED"]
    with session_scope() as session:
        sell = session.execute(select(Order).order_by(Order.id.desc())).scalars().first()
        position = session.execute(select(Position)).scalar_one()
    assert stack["manager"]._key_action(sell.order_key) == "SELL"
    assert abs(sell.filled_size - int(s0_shares * 100) / 100) < 1e-6
    assert abs(position.size - (s0_shares + s1_shares - sell.filled_size)) < 1e-6
    assert position.status == "open" and position.size >= s1_shares - 1e-6
    # nothing more is sold for the stage 1 shares
    assert await stack["manager"].handle_exits(late, books1, "paper", stack["config"]) == []


async def test_stage0_sell_pnl_uses_stage0_cost_not_blended_average():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    with session_scope() as session:
        s0 = session.execute(select(Order)).scalar_one()
        s0_cost = s0.filled_size * s0.avg_fill_price

    # stage 1 adds shares at a very different price (0.99) to the same UP position
    late = make_market(remaining=45)
    books1 = books_for(late, up_ask=0.99, down_ask=0.01)
    stage_books(stack, late, books1)
    evaluation = stack["strategy"].evaluate(late, books1, int(time.time() * 1000), stack["config"])
    evaluation.signals = [s for s in evaluation.signals if s.stage == 1 and s.side == "UP"]
    ctx = make_ctx(remaining=45, btc_price=100_150.0, price_to_beat=100_000.0)
    await stack["manager"].handle_event(late, evaluation, books1, "paper", ctx)

    books1[late.up_token_id].bids = {0.99: 100.0}
    await stack["manager"].handle_exits(late, books1, "paper", stack["config"])
    with session_scope() as session:
        sell = session.execute(select(Order).order_by(Order.id.desc())).scalars().first()
        position = session.execute(select(Position)).scalar_one()
        sold = sell.filled_size
        proceeds = sold * sell.avg_fill_price
        cost_of_sold = sold * (s0_cost / s0.filled_size if False else s0.avg_fill_price)
        # realised = sale proceeds - sell fee - exact stage 0 cost - stage 0 buy fee share
        expected = proceeds - sell.fee - cost_of_sold - s0.fee * (sold / s0.filled_size)
        assert abs(position.realized_pnl - expected) < 1e-6
        # the cost still held belongs to the unsold shares (stage 1 + the sub-cent leftover)
        assert position.cost_basis > 0 and position.status == "open"


def test_risk_blocks_stage0_ask_below_minimum():
    from dataclasses import replace

    config = make_config(**STAGE0)
    good = make_ctx(remaining=150, btc_price=100_150.0, price_to_beat=100_000.0)
    signal = _signal(up=0.70)
    assert RiskManager().check(signal, good, config).approved
    low = RiskManager().check(replace(signal, best_ask=0.55), good, config)
    assert low.approved is False and low.reason == "stage0_ask_too_low"
    assert RiskManager().check(replace(signal, best_ask=0.60), good, config).approved


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "btc, up_ask, down_ask, expected",
    [
        (100_080.0, 0.70, 0.30, "UP"),    # exactly +80 above the price to beat -> UP
        (100_250.0, 0.70, 0.30, "UP"),    # +250
        (100_079.0, 0.70, 0.30, None),    # +79: gap too small
        (99_920.0, 0.30, 0.70, "DOWN"),   # exactly -80 below the price to beat -> DOWN
        (99_750.0, 0.30, 0.70, "DOWN"),   # -250
        (99_921.0, 0.30, 0.70, None),     # -79: gap too small
        (100_150.0, 0.86, 0.70, None),    # UP has the gap but its ask is above .85; DOWN has no gap
        (99_850.0, 0.70, 0.86, None),     # DOWN has the gap but its ask is above .85; UP has no gap
        (100_150.0, 0.55, 0.45, None),    # UP has the gap but its ask is below .60
        (100_000.0, 0.70, 0.70, None),    # no gap at all, both asks in range
    ],
)
async def test_bot_stage0_buys_up_above_plus80_and_down_below_minus80(btc, up_ask, down_ask, expected):
    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(btc, 100_000.0, ""))  # (BTC price, price to beat)
    bot.data._books = dict(books_for(market, up_ask=up_ask, down_ask=down_ask))

    await _event(bot, market)
    with session_scope() as session:
        orders = session.execute(select(Order)).scalars().all()
    assert [o.side for o in orders] == ([expected] if expected else [])


# ---------------------------------------------------------------- stage 1 / stage 2 take-profit
STAGE1_SELL = dict(stage1_sell_enabled=True)  # cost <= 0.86 -> sold at +10 %, dearer -> sold at 0.99
STAGE2_SELL = dict(stage2_sell_enabled=True)  # sold at 0.99


def test_stage1_and_stage2_sell_price_rules():
    config = make_config(**STAGE1_SELL, **STAGE2_SELL)
    for cost in (0.80, 0.85, 0.86):
        assert config.stage1_sell_price_for(cost) == pytest.approx(cost * 1.10)
    for cost in (0.87, 0.93, 0.99):
        assert config.stage1_sell_price_for(cost) == 0.99
    assert config.stage1_sell_price_for(None) == 0.99  # unknown cost: highest price, never sells too early
    assert config.stage2_sell_price_for(0.92) == 0.99 and config.stage2_sell_price_for(None) == 0.99
    off = make_config()
    assert off.stage1_sell_price_for(0.85) is None and off.stage2_sell_price_for(0.95) is None


async def _stage_buy(stack, market, books, stage, side="UP", **ctx_overrides):
    stage_books(stack, market, books)
    now_ms = int(time.time() * 1000)
    evaluation = stack["strategy"].evaluate(market, books, now_ms, stack["config"])
    evaluation.signals = [s for s in evaluation.signals if s.stage == stage and s.side == side]
    assert evaluation.signals, f"stage {stage} should have fired"
    ctx = make_ctx(
        remaining=market.remaining_seconds(), btc_price=100_150.0, price_to_beat=100_000.0, **ctx_overrides
    )
    return await stack["manager"].handle_event(market, evaluation, books, "paper", ctx)


async def _stage1_buy(stack, market, books, side="UP"):
    return await _stage_buy(stack, market, books, 1, side)


@pytest.mark.parametrize(
    "buy_ask, too_low, sold_at",
    # 0.80 -> +10 % = 0.88, 0.85 -> 0.935 (bid 0.94), 0.86 -> 0.946 (bid 0.95); dearer than 0.86 -> 0.99
    [(0.80, 0.87, 0.88), (0.85, 0.93, 0.94), (0.86, 0.94, 0.95), (0.90, 0.98, 0.99), (0.95, 0.98, 0.99)],
)
async def test_stage1_sells_at_10_percent_when_bought_up_to_86_else_at_99(buy_ask, too_low, sold_at):
    stack = build_stack(make_config(**STAGE0, **STAGE1_SELL, trade_amount_usdc=4.0))
    market = make_market(remaining=55)
    books = books_for(market, up_ask=buy_ask, down_ask=0.05)
    assert [r.status for r in await _stage1_buy(stack, market, books)] == ["FILLED"]

    books[market.up_token_id].bids = {too_low: 100.0}  # one cent short: hold
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []

    books[market.up_token_id].bids = {sold_at: 100.0}
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [r.status for r in results] == ["FILLED"] and results[0].reason == "stage1_sell"
    manager = stack["manager"]
    with session_scope() as session:
        orders = session.execute(select(Order).order_by(Order.id)).scalars().all()
        position = session.execute(select(Position)).scalar_one()
    assert [(manager._key_action(o.order_key), manager._key_stage(o.order_key)) for o in orders] == [
        ("BUY", 1), ("SELL", 1),
    ]
    assert orders[1].avg_fill_price >= sold_at - 1e-9
    assert position.status == "closed"
    assert await manager.handle_exits(market, books, "paper", stack["config"]) == []  # nothing left


async def test_stage1_take_profit_can_be_switched_off():
    stack = build_stack(make_config(**STAGE0, stage1_sell_enabled=False, trade_amount_usdc=4.0))
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.85, down_ask=0.10)
    await _stage1_buy(stack, market, books)
    books[market.up_token_id].bids = {0.99: 100.0}
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []


@pytest.mark.parametrize("buy_ask", [0.90, 0.95])
async def test_stage2_sells_at_99(buy_ask):
    stack = build_stack(make_config(**STAGE0, **STAGE2_SELL, trade_amount_usdc=4.0))
    market = make_market(remaining=40)
    books = books_for(market, up_ask=buy_ask, down_ask=0.05)
    results = await _stage_buy(stack, market, books, 2, stage1_bought=True, stage1_remaining=70.0)
    assert [r.status for r in results] == ["FILLED"]

    books[market.up_token_id].bids = {0.98: 100.0}
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []
    books[market.up_token_id].bids = {0.99: 100.0}
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [r.status for r in results] == ["FILLED"] and results[0].reason == "stage2_sell"
    manager = stack["manager"]
    with session_scope() as session:
        orders = session.execute(select(Order).order_by(Order.id)).scalars().all()
    assert [(manager._key_action(o.order_key), manager._key_stage(o.order_key)) for o in orders] == [
        ("BUY", 2), ("SELL", 2),
    ]


async def test_stage2_take_profit_can_be_switched_off():
    stack = build_stack(make_config(**STAGE0, stage2_sell_enabled=False, trade_amount_usdc=4.0))
    market = make_market(remaining=40)
    books = books_for(market, up_ask=0.92, down_ask=0.05)
    await _stage_buy(stack, market, books, 2, stage1_bought=True, stage1_remaining=70.0)
    books[market.up_token_id].bids = {0.99: 100.0}
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []


async def test_stages_sell_only_their_own_shares_on_the_same_side():
    stack = build_stack(
        make_config(**STAGE0, **STAGE1_SELL, **STAGE2_SELL, trade_amount_usdc=4.0, paper_starting_balance=30.0)
    )
    early = make_market(remaining=150)
    await _stage0_buy(stack, early, books_for(early, up_ask=0.75, down_ask=0.10))  # stage 0 @ .75 -> sells .90
    late = make_market(remaining=55)
    late_books = books_for(late, up_ask=0.85, down_ask=0.10)
    await _stage1_buy(stack, late, late_books)  # stage 1 @ .85 -> sells .95
    last = make_market(remaining=40)
    last_books = books_for(last, up_ask=0.92, down_ask=0.05)
    await _stage_buy(stack, last, last_books, 2, stage1_bought=True, stage1_remaining=55.0)  # stage 2 -> sells .99
    manager = stack["manager"]

    def bid(price):
        last_books[last.up_token_id].bids = {price: 100.0}
        return manager.handle_exits(last, last_books, "paper", stack["config"])

    assert [r.reason for r in await bid(0.90)] == ["stage0_sell"]
    assert [r.reason for r in await bid(0.95)] == ["stage1_sell"]
    assert await bid(0.97) == []  # stage 2 waits for .99
    assert [r.reason for r in await bid(0.99)] == ["stage2_sell"]
    for stage in (0, 1, 2):
        assert manager.load_stage0_holding("paper", last.market_id, stage) is None
    with session_scope() as session:
        position = session.execute(select(Position)).scalar_one()
    # every stage sold all whole-cent shares; only rounding dust (< 0.02 share in total) is left to settle
    assert position.size < 0.02 and position.realized_pnl > 0


async def test_stage1_sell_does_not_break_the_stage_chain():
    stack = build_stack(make_config(**STAGE0, **STAGE1_SELL, trade_amount_usdc=4.0))
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.85, down_ask=0.10)
    await _stage1_buy(stack, market, books)
    books[market.up_token_id].bids = {0.95: 100.0}
    await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    # the stage 1 BUY still counts as the stage 1 entry; its SELL row is not a second entry
    assert stack["manager"].stage1_remaining("paper", market.market_id) is not None
    with session_scope() as session:
        orders = session.execute(select(Order)).scalars().all()
    buys = [o for o in orders if stack["manager"]._key_action(o.order_key) == "BUY"]
    assert len(buys) == 1


async def test_bot_stage1_buys_in_final_window_then_sells_at_10_percent_profit():
    market = make_market(remaining=55)
    bot = _running_bot(market, btc=(100_150.0, 100_000.0, ""))  # BTC $150 above the price to beat
    books = books_for(market, up_ask=0.85, down_ask=0.10)
    bot.data._books = dict(books)
    await _event(bot, market)
    with session_scope() as session:
        orders = session.execute(select(Order)).scalars().all()
    assert [(bot.orders._key_stage(o.order_key), o.side, o.status) for o in orders] == [(1, "UP", "FILLED")]

    # bought at 0.85 (<= 0.86): +10 % -> sell once the best bid is 0.935 or more
    books[market.up_token_id].bids = {0.93: 100.0}
    await _event(bot, market)
    with session_scope() as session:
        assert len(session.execute(select(Order)).scalars().all()) == 1  # bid below 0.935: still held

    books[market.up_token_id].bids = {0.94: 100.0}
    await _event(bot, market)
    rows = await bot.recent_orders("paper")
    stage1_rows = [r for r in rows if r["stage"] == 1]
    assert sorted((r["action"], r["status"]) for r in stage1_rows) == [("BUY", "FILLED"), ("SELL", "FILLED")]
    snap = await bot.snapshot()
    assert "stage1_sell_price" not in snap["strategy"] and snap["strategy"]["stage1_sell_price_high"] == 0.99
    assert snap["strategy"]["stage1_sell_max_buy"] == 0.86 and snap["strategy"]["stage1_take_profit_pct"] == 10.0
    assert snap["strategy"]["stage2_sell_price"] == 0.99


def test_stage1_cheap_buys_sell_at_10_percent_profit():
    config = make_config(stage1_sell_enabled=True, stage1_sell_max_buy=0.86, stage1_take_profit_pct=10.0)
    assert config.stage1_sell_price_for(0.81) == pytest.approx(0.891)
    assert config.stage1_sell_price_for(0.80) == pytest.approx(0.88)
    assert config.stage1_sell_price_for(0.86) == pytest.approx(0.946)  # the limit itself is still "cheap"
    assert config.stage1_sell_price_for(0.87) == 0.99  # dearer than 0.86: the high fixed price
    assert config.stage1_sell_price_for(None) == 0.99
    # 0 % = no take-profit for cheap buys (held to settlement / stop-loss); dearer buys keep the 0.99 sell
    none = make_config(stage1_sell_enabled=True, stage1_sell_max_buy=0.86, stage1_take_profit_pct=0.0)
    assert none.stage1_sell_price_for(0.81) is None and none.stage1_sell_price_for(0.90) == 0.99


# ---------------------------------------------------------------- tiered percent take-profit
def test_stage0_take_profit_tiers_15_10_5_by_buy_price():
    config = make_config()
    # 0.60-0.70 -> +15 %, 0.70-0.80 -> +10 %, 0.80-0.85 -> +5 %
    assert config.stage0_take_profit_pct_for(0.60) == 15.0 and config.stage0_take_profit_pct_for(0.69) == 15.0
    assert config.stage0_take_profit_pct_for(0.70) == 10.0 and config.stage0_take_profit_pct_for(0.79) == 10.0
    assert config.stage0_take_profit_pct_for(0.80) == 5.0 and config.stage0_take_profit_pct_for(0.85) == 5.0
    assert config.stage0_sell_price_for(0.60) == pytest.approx(0.69)
    assert config.stage0_sell_price_for(0.65) == pytest.approx(0.7475)
    assert config.stage0_sell_price_for(0.70) == pytest.approx(0.77)
    assert config.stage0_sell_price_for(0.75) == pytest.approx(0.825)
    assert config.stage0_sell_price_for(0.80) == pytest.approx(0.84)
    assert config.stage0_sell_price_for(0.85) == pytest.approx(0.8925)
    assert config.stage0_sell_price_for(None) is None  # unknown cost: no reference price, held to settlement
    assert make_config(stage0_take_profit_pct_mid=0.0).stage0_sell_price_for(0.75) is None  # a tier set to 0: off
    assert make_config(stage0_take_profit_pct_high=50.0).stage0_sell_price_for(0.85) == 0.99  # capped at 0.99


def test_stage0_defaults_are_max_ask_85_and_tiers_15_10_5():
    fields = Settings.model_fields
    assert fields["stage0_max_ask"].default == 0.85
    assert (fields["stage0_mid_from_ask"].default, fields["stage0_high_from_ask"].default) == (0.70, 0.80)
    assert [fields[f"stage0_take_profit_pct_{t}"].default for t in ("low", "mid", "high")] == [15.0, 10.0, 5.0]


def test_stage0_tier_borders_must_be_ordered():
    with pytest.raises(ValueError):
        make_config(stage0_mid_from_ask=0.80, stage0_high_from_ask=0.70)


@pytest.mark.parametrize(
    "buy_ask, too_low, sold_at",
    [(0.60, 0.68, 0.69), (0.65, 0.74, 0.75), (0.70, 0.76, 0.77), (0.75, 0.82, 0.83), (0.80, 0.83, 0.84), (0.85, 0.89, 0.90)],
)
async def test_stage0_sells_at_the_tier_percent_of_the_buy_price(buy_ask, too_low, sold_at):
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=buy_ask, down_ask=0.10)
    assert [r.status for r in await _stage0_buy(stack, market, books)] == ["FILLED"]
    manager = stack["manager"]
    up = books[market.up_token_id]
    up.bids = {too_low: 100.0}  # one cent under the take-profit price: hold
    assert await manager.handle_exits(market, books, "paper", stack["config"]) == []
    up.bids = {sold_at: 100.0}
    results = await manager.handle_exits(market, books, "paper", stack["config"])
    assert [(r.status, r.reason) for r in results] == [("FILLED", "stage0_sell")]
    with session_scope() as session:
        sell = [o for o in session.execute(select(Order).order_by(Order.id)).scalars() if "sell" in (o.order_key or "")]
    assert len(sell) == 1 and sell[0].avg_fill_price >= sold_at - 1e-9


async def test_stage0_tier_set_to_zero_holds_to_settlement():
    stack = build_stack(make_config(**STAGE0, stage0_take_profit_pct_mid=0.0, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.75, down_ask=0.10)
    assert [r.status for r in await _stage0_buy(stack, market, books)] == ["FILLED"]
    books[market.up_token_id].bids = {0.99: 100.0}
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []
