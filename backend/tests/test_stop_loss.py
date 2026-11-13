"""Stop-loss: every stage sells its own shares at the market once the best bid is `pct` percent
below the average price it paid (stage 0 = 20 %, stages 1/2/3 = 15 % by default)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings
from app.database.models import Order, Position
from app.database.session import session_scope
from app.main import create_app

from .helpers import books_for, build_stack, make_config, make_market
from .test_stage0 import STAGE0, _event, _running_bot, _stage0_buy, _stage_buy

STOPS = dict(
    stage0_stop_loss_pct=20.0,
    stage1_stop_loss_pct=15.0,
    stage2_stop_loss_pct=5.0,
    stage3_stop_loss_pct=5.0,
)


# ---------------------------------------------------------------- configuration
def test_stop_loss_price_is_a_percentage_below_the_average_buy_price():
    config = make_config(**STOPS)
    assert config.stop_loss_price_for(0, 0.70) == pytest.approx(0.56)
    assert config.stop_loss_price_for(1, 0.85) == pytest.approx(0.7225)
    assert config.stop_loss_price_for(2, 0.92) == pytest.approx(0.874)
    assert config.stop_loss_price_for(3, 0.90) == pytest.approx(0.855)
    assert config.stop_loss_price_for(0, None) is None  # no reference price, no stop
    assert config.stop_loss_price_for(4, 0.90) is None  # not an entry stage


def test_stop_loss_is_off_at_zero_and_when_settings_are_built_directly():
    assert make_config().stop_loss_price_for(0, 0.70) is None
    only_stage1 = make_config(stage0_stop_loss_pct=0.0, stage1_stop_loss_pct=15.0)
    assert only_stage1.stop_loss_price_for(0, 0.70) is None
    assert only_stage1.stop_loss_price_for(1, 0.85) == pytest.approx(0.7225)


def test_app_defaults_are_20_percent_for_stage0_15_for_stage1_and_5_for_stages_2_and_3():
    fields = Settings.model_fields
    assert fields["stage0_stop_loss_pct"].default == 40.0
    assert fields["stage1_stop_loss_pct"].default == 15.0
    assert all(fields[f"stage{n}_stop_loss_pct"].default == 5.0 for n in (2, 3))


def test_stop_loss_percent_is_validated():
    with pytest.raises(ValueError):
        make_config(stage0_stop_loss_pct=100.0)
    with pytest.raises(ValueError):
        make_config(stage1_stop_loss_pct=-1.0)


def test_stop_loss_settings_can_be_patched_through_the_api():
    with TestClient(create_app()) as client:
        ok = client.patch("/api/settings", json={"stage0_stop_loss_pct": 25, "stage3_stop_loss_pct": 0})
        assert ok.status_code == 200
        assert ok.json()["stage0_stop_loss_pct"] == 25
        assert ok.json()["stage3_stop_loss_pct"] == 0
        assert client.patch("/api/settings", json={"stage1_stop_loss_pct": 100}).status_code in {400, 422}
        assert client.get("/api/settings").json()["stage0_stop_loss_pct"] == 25


# ---------------------------------------------------------------- stage 0
async def test_stage0_stop_loss_sells_at_20_percent_under_the_buy_price():
    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    assert [r.status for r in await _stage0_buy(stack, market, books)] == ["FILLED"]
    manager = stack["manager"]
    up = books[market.up_token_id]

    up.bids = {0.65: 100.0}  # 18.75 % under 0.80: not yet
    assert await manager.handle_exits(market, books, "paper", stack["config"]) == []

    up.bids = {0.64: 100.0}  # exactly 20 % under 0.80
    results = await manager.handle_exits(market, books, "paper", stack["config"])
    assert [(r.status, r.reason) for r in results] == [("FILLED", "stage0_stop_loss")]
    with session_scope() as session:
        orders = session.execute(select(Order).order_by(Order.id)).scalars().all()
        position = session.execute(select(Position)).scalar_one()
    assert [manager._key_action(o.order_key) for o in orders] == ["BUY", "SELL"]
    assert orders[1].reason == "stage0_stop_loss"
    assert orders[1].filled_size == pytest.approx(5.0)
    assert orders[1].avg_fill_price == pytest.approx(0.64)
    assert position.status == "closed" and position.realized_pnl < 0
    # everything is sold: another exit pass does nothing
    assert await manager.handle_exits(market, books, "paper", stack["config"]) == []


async def test_stop_loss_is_a_market_sell_even_when_the_bid_is_far_below_the_stop():
    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    books[market.up_token_id].bids = {0.30: 100.0}  # the bid fell through the stop (0.64)
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [(r.status, r.reason) for r in results] == [("FILLED", "stage0_stop_loss")]
    with session_scope() as session:
        sell = session.execute(select(Order).order_by(Order.id.desc())).scalars().first()
    assert sell.avg_fill_price == pytest.approx(0.30)


async def test_take_profit_still_sells_at_its_price_with_the_stop_loss_on():
    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)  # bought at 0.80 -> high tier: take-profit at +5 % = 0.84
    await _stage0_buy(stack, market, books)
    books[market.up_token_id].bids = {0.83: 100.0}  # between the stop (0.64) and the take-profit
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []
    books[market.up_token_id].bids = {0.84: 100.0}
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [(r.status, r.reason) for r in results] == [("FILLED", "stage0_sell")]


async def test_stop_loss_off_holds_the_position_whatever_the_bid_does():
    stack = build_stack(make_config(**STAGE0, trade_amount_usdc=4.0))  # all stop-loss percents 0
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    books[market.up_token_id].bids = {0.10: 100.0}
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []


# ---------------------------------------------------------------- exit speed
def test_exit_retry_gap_is_short_by_default():
    assert Settings().exit_retry_gap_ms == 50


async def test_failed_exit_sell_is_retried_on_the_exit_gap_not_the_entry_gap():
    from app.execution.base import ExecutionReport

    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    manager = stack["manager"]
    paper = manager._executor_provider("paper")
    calls = {"n": 0}

    class FlakyExit:
        async def place_sell(self, request):
            calls["n"] += 1
            if calls["n"] == 1:
                return ExecutionReport(status="CANCELED", error="FOK sell was not filled")
            return await paper.place_sell(request)

    manager._executor_provider = lambda _mode: FlakyExit()
    books[market.up_token_id].bids = {0.30: 100.0}  # through the stop (0.64)

    manager._retry_gap_ms = 60_000  # a long ENTRY gap must not slow an exit down
    manager._exit_retry_gap_ms = 60_000
    first = await manager.handle_exits(market, books, "paper", stack["config"])
    assert [r.status for r in first] == ["CANCELED"]
    # still inside the exit gap: held back, nothing is sent
    assert await manager.handle_exits(market, books, "paper", stack["config"]) == []
    assert calls["n"] == 1

    manager._exit_retry_gap_ms = 0  # the exit gap elapsed (the entry gap is still 60 s)
    again = await manager.handle_exits(market, books, "paper", stack["config"])
    assert [(r.status, r.reason) for r in again] == [("FILLED", "stage0_stop_loss")]
    assert calls["n"] == 2


async def test_retry_loop_wakes_every_50ms_while_shares_are_held():
    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(99_850.0, 100_000.0, ""))  # BTC is $150 BELOW the price to beat
    bot.data._books = dict(books_for(market, up_ask=0.40, down_ask=0.80))

    assert bot._retry_timing() == (0.5, 500)  # nothing held: the normal safety-net cadence

    await _event(bot, market)  # stage 0 buys DOWN
    assert bot._retry_timing() == (0.05, 50)  # shares held: exits are re-checked fast


async def test_stopped_out_stage_is_not_bought_again_in_the_same_market():
    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)
    await _stage0_buy(stack, market, books)
    books[market.up_token_id].bids = {0.60: 100.0}
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [r.reason for r in results] == ["stage0_stop_loss"]

    again = await _stage0_buy(stack, market, books)  # the ask is still inside the stage 0 range
    assert all(r.status in {"BLOCKED", "SKIPPED"} for r in again)
    with session_scope() as session:
        orders = session.execute(select(Order)).scalars().all()
    assert [stack["manager"]._key_action(o.order_key) for o in orders].count("BUY") == 1


async def test_stop_loss_compares_the_best_bid_with_the_buy_price():
    """A wide bid/ask gap right after the buy counts: ask 0.80 with the helper's 0.50 bid is 37.5 % under."""
    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0))
    market = make_market(remaining=150)
    books = books_for(market, up_ask=0.80, down_ask=0.10)  # default bid of the test book: 0.50
    await _stage0_buy(stack, market, books)
    results = await stack["manager"].handle_exits(market, books, "paper", stack["config"])
    assert [(r.status, r.reason) for r in results] == [("FILLED", "stage0_stop_loss")]


# ---------------------------------------------------------------- stages 1, 2, 3
@pytest.mark.parametrize(
    "stage, remaining, buy_ask, ctx, above_stop_bid, stop_bid",
    [
        (1, 55, 0.85, {}, 0.73, 0.72),  # stop = 0.85 * 0.85 = 0.7225
        (2, 40, 0.92, dict(stage1_bought=True, stage1_remaining=70.0), 0.88, 0.87),  # stop = 0.92 * 0.95 = 0.874
        (3, 25, 0.92, {}, 0.88, 0.87),  # stop = 0.874
    ],
)
async def test_stage_1_2_3_stop_loss_sells_at_its_percent_under_the_buy_price(
    stage, remaining, buy_ask, ctx, above_stop_bid, stop_bid
):
    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0))
    market = make_market(remaining=remaining)
    books = books_for(market, up_ask=buy_ask, down_ask=0.05)
    assert [r.status for r in await _stage_buy(stack, market, books, stage, **ctx)] == ["FILLED"]
    manager = stack["manager"]
    up = books[market.up_token_id]

    up.bids = {above_stop_bid: 100.0}  # less than the stop-loss percent under the buy price
    assert await manager.handle_exits(market, books, "paper", stack["config"]) == []

    up.bids = {stop_bid: 100.0}  # more than the stop-loss percent under it
    results = await manager.handle_exits(market, books, "paper", stack["config"])
    assert [(r.status, r.reason) for r in results] == [("FILLED", f"stage{stage}_stop_loss")]
    with session_scope() as session:
        orders = session.execute(select(Order).order_by(Order.id)).scalars().all()
        position = session.execute(select(Position)).scalar_one()
    assert [(manager._key_action(o.order_key), manager._key_stage(o.order_key)) for o in orders] == [
        ("BUY", stage),
        ("SELL", stage),
    ]
    assert position.status == "closed" and position.realized_pnl < 0
    assert await manager.handle_exits(market, books, "paper", stack["config"]) == []


async def test_stage3_has_no_take_profit_only_the_stop_loss():
    stack = build_stack(
        make_config(
            **STAGE0, **STOPS, stage1_sell_enabled=True, stage2_sell_enabled=True, trade_amount_usdc=4.0
        )
    )
    market = make_market(remaining=25)
    books = books_for(market, up_ask=0.92, down_ask=0.05)
    assert [r.status for r in await _stage_buy(stack, market, books, 3)] == ["FILLED"]
    books[market.up_token_id].bids = {0.99: 100.0}  # no stage 3 take-profit: held to settlement
    assert await stack["manager"].handle_exits(market, books, "paper", stack["config"]) == []
    # the stop-loss is armed even though the exit watch was quiet before
    assert stack["manager"].has_stage0_holding("paper", market.market_id) is True


async def test_each_stage_stops_out_only_its_own_shares():
    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0, paper_starting_balance=30.0))
    early = make_market(remaining=150)
    await _stage0_buy(stack, early, books_for(early, up_ask=0.80, down_ask=0.10))  # stage 0 stop: 0.64
    late = make_market(remaining=55)
    late_books = books_for(late, up_ask=0.90, down_ask=0.05)
    assert [r.status for r in await _stage_buy(stack, late, late_books, 1)] == ["FILLED"]  # stage 1 stop: 0.765
    manager = stack["manager"]

    def bid(price):
        late_books[late.up_token_id].bids = {price: 100.0}
        return manager.handle_exits(late, late_books, "paper", stack["config"])

    assert await bid(0.77) == []  # above both stops
    assert [r.reason for r in await bid(0.70)] == ["stage1_stop_loss"]  # stage 0's stop (0.64) is lower
    assert await bid(0.70) == []  # stage 1 is sold, stage 0 still waits
    assert [r.reason for r in await bid(0.60)] == ["stage0_stop_loss"]
    with session_scope() as session:
        position = session.execute(select(Position)).scalar_one()
    assert position.size < 0.02  # only sub-cent rounding dust is left to settle


# ---------------------------------------------------------------- whole bot
async def test_bot_stops_out_stage0_reports_a_loss_and_does_not_rebuy():
    market = make_market(remaining=150)
    bot = _running_bot(market, btc=(99_850.0, 100_000.0, ""))  # BTC $150 below the price to beat -> DOWN
    with session_scope() as session:
        bot.runtime.update(session, {"stage0_stop_loss_pct": 20.0})
    books = books_for(market, up_ask=0.40, down_ask=0.80)
    books[market.down_token_id].bids = {0.79: 100.0}  # a normal spread: no stop right after the buy
    bot.data._books = dict(books)

    await _event(bot, market)
    with session_scope() as session:
        orders = session.execute(select(Order)).scalars().all()
    assert [(o.side, o.status) for o in orders] == [("DOWN", "FILLED")]

    books[market.down_token_id].bids = {0.63: 100.0}  # stop = 0.80 * 0.8 = 0.64
    await _event(bot, market)
    rows = await bot.recent_orders("paper")
    assert sorted((r["action"], r["stage"], r["status"]) for r in rows) == [
        ("BUY", 0, "FILLED"),
        ("SELL", 0, "FILLED"),
    ]
    buy = next(r for r in rows if r["action"] == "BUY")
    assert buy["result"] == "LOSS" and buy["result_pnl"] < 0
    snap = await bot.snapshot()
    assert snap["positions"] == []
    assert snap["account"]["balance"] < 10.0
    assert snap["strategy"]["stage0_stop_loss_pct"] == 20.0
    assert snap["strategy"]["stage1_stop_loss_pct"] == 0.0  # the test environment switches the others off

    await _event(bot, market)  # nothing more happens in this market
    with session_scope() as session:
        assert len(session.execute(select(Order)).scalars().all()) == 2


async def test_stage_1_2_3_in_one_market_each_stops_out_alone():
    """All three add-on stages hold the same side; every stop-loss sells only its own shares."""
    stack = build_stack(make_config(**STAGE0, **STOPS, trade_amount_usdc=4.0, paper_starting_balance=30.0))
    manager = stack["manager"]
    m1, m2, m3 = make_market(remaining=55), make_market(remaining=40), make_market(remaining=25)
    # stage 1 bought at 0.90 (stop 0.765), stage 2 at 0.95 (stop 0.9025), stage 3 at 0.92 (stop 0.874)
    for market, stage, ask, ctx in (
        (m1, 1, 0.90, {}),
        (m2, 2, 0.95, dict(stage1_bought=True, stage1_remaining=55.0)),
        (m3, 3, 0.92, {}),
    ):
        books = books_for(market, up_ask=ask, down_ask=0.04)
        assert [r.status for r in await _stage_buy(stack, market, books, stage, **ctx)] == ["FILLED"]
    books = books_for(m3, up_ask=0.92, down_ask=0.04)

    def bid(price):
        books[m3.up_token_id].bids = {price: 100.0}
        return manager.handle_exits(m3, books, "paper", stack["config"])

    assert await bid(0.91) == []  # above every stop
    assert [r.reason for r in await bid(0.90)] == ["stage2_stop_loss"]  # only stage 2 (stop 0.9025)
    assert await bid(0.90) == []  # stage 1 (0.765) and stage 3 (0.874) keep their shares
    assert [r.reason for r in await bid(0.87)] == ["stage3_stop_loss"]
    assert [r.reason for r in await bid(0.70)] == ["stage1_stop_loss"]
    with session_scope() as session:
        position = session.execute(select(Position)).scalar_one()
    assert position.size < 0.03  # only sub-cent rounding dust of the three stages is left to settle
    assert all(manager.load_stage0_holding("paper", m1.market_id, st) is None for st in (1, 2, 3))
