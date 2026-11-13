from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.database.models import Order, TriggerEvent
from app.database.session import session_scope
from app.execution.base import ExecutionReport, FillPart

from .helpers import (
    books_for,
    build_stack,
    make_book,
    make_config,
    make_ctx,
    make_market,
    stage_books,
)


def _evaluate(stack, market, books):
    now_ms = int(time.time() * 1000)
    return stack["strategy"].evaluate(market, books, now_ms, stack["config"])


async def _submit(stack, market, books, ctx=None):
    stage_books(stack, market, books)
    evaluation = _evaluate(stack, market, books)
    ctx = ctx or make_ctx(remaining=market.remaining_seconds())
    results = await stack["manager"].handle_event(
        market, evaluation, books, "paper", ctx
    )
    return evaluation, results


def _orders() -> list[Order]:
    with session_scope() as session:
        return list(session.execute(select(Order)).scalars().all())


async def test_order_uses_current_market_ask_not_threshold():
    stack = build_stack()
    market = make_market(remaining=55)  # stage 1 only (stage 2 starts at 50s)
    books = books_for(market, up_ask=0.93, down_ask=0.07)

    _, results = await _submit(stack, market, books)

    assert len(results) == 1
    assert results[0].status == "FILLED"
    orders = _orders()
    assert len(orders) == 1
    order = orders[0]
    assert order.side == "UP"
    assert order.requested_price == 0.93
    assert order.submitted_price == 0.93
    assert order.avg_fill_price == 0.93
    assert order.requested_price != 0.80
    assert order.filled_size > 0
    assert order.fee > 0
    assert order.latency_ms is not None
    assert order.latency_ms < 5000


async def test_no_fixed_limit_price_order():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.97, down_ask=0.03)

    await _submit(stack, market, books)

    order = _orders()[0]
    # The 0.80 threshold must never appear as an execution or limit price.
    assert order.requested_price == 0.97
    assert order.avg_fill_price >= 0.97
    assert order.submitted_price != 0.80


async def test_duplicate_orders_are_blocked():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.90, down_ask=0.10)

    await _submit(stack, market, books)
    _, results = await _submit(stack, market, books)

    assert len(_orders()) == 1
    assert results[0].status == "BLOCKED"
    assert results[0].reason == "duplicate"
    await stack["manager"].close()
    with session_scope() as session:
        events = session.execute(select(TriggerEvent)).scalars().all()
    assert any(e.decision == "blocked_duplicate" for e in events)


async def test_concurrent_triggers_create_one_order():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.90, down_ask=0.10)
    stack["books"].update(books)
    evaluation = _evaluate(stack, market, books)
    ctx = make_ctx(remaining=market.remaining_seconds())

    await asyncio.gather(
        stack["manager"].handle_event(market, evaluation, books, "paper", ctx),
        stack["manager"].handle_event(market, evaluation, books, "paper", ctx),
    )
    assert len(_orders()) == 1


async def test_stop_waiter_drains_submission_handlers_already_in_flight():
    stack = build_stack()
    manager = stack["manager"]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def in_flight_handler():
        async with manager._lock:
            entered.set()
            await release.wait()

    active = asyncio.create_task(in_flight_handler())
    await entered.wait()
    drained = asyncio.create_task(manager.wait_for_submissions())
    await asyncio.sleep(0)
    assert not drained.done()
    release.set()
    await asyncio.gather(active, drained)


async def test_conflict_policy_up_first_selects_one_side():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.88, down_ask=0.86)

    _, results = await _submit(stack, market, books)

    orders = _orders()
    assert len(orders) == 1
    assert orders[0].side == "UP"
    assert any(r.reason == "conflict_not_selected" for r in results)


async def test_conflict_policy_block_both_places_no_order():
    stack = build_stack(make_config(conflict_policy="block_both"))
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.88, down_ask=0.86)

    _, results = await _submit(stack, market, books)

    assert _orders() == []
    assert all(r.status == "BLOCKED" for r in results)


async def test_stale_market_data_blocks_execution():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, 0.90, 0.10, up_age_ms=5000)

    evaluation, results = await _submit(stack, market, books)

    assert evaluation.signals, "price qualifies, so the block must come from freshness"
    assert results[0].reason == "stale_data"
    assert _orders() == []
    await stack["manager"].close()
    with session_scope() as session:
        events = session.execute(select(TriggerEvent)).scalars().all()
    assert any(e.decision == "blocked_stale_data" for e in events)


async def test_expired_market_blocks_execution():
    stack = build_stack()
    market = make_market(remaining=-2)
    books = books_for(market, up_ask=0.95, down_ask=0.95)

    _, results = await _submit(stack, market, books, make_ctx(remaining=-2))

    assert results == []
    assert _orders() == []


async def test_max_execution_price_blocks():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.995, down_ask=0.005)

    _, results = await _submit(stack, market, books)

    assert results[0].reason == "max_execution_price_exceeded"
    assert _orders() == []


async def test_partial_fill_then_retry_completes():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.85, down_ask=0.10, up_size=3.0)

    _, results = await _submit(stack, market, books)
    assert results[0].status == "PARTIALLY_FILLED"
    first = _orders()[0]
    assert 0 < first.filled_size < 5.0 / 0.85

    stack["manager"]._retry_after.clear()
    more = books_for(market, up_ask=0.85, down_ask=0.10)
    _, results = await _submit(stack, market, more)

    final = _orders()[0]
    assert final.status == "FILLED"
    assert final.filled_size == pytest.approx(5.0 / 0.85, rel=0.01)


async def test_stop_drain_waits_for_paper_partial_retry_and_blocks_next_retry():
    stack = build_stack()
    market = make_market(remaining=55)
    partial_books = books_for(market, up_ask=0.85, down_ask=0.10, up_size=2.0)
    await _submit(stack, market, partial_books)
    assert _orders()[0].status == "PARTIALLY_FILLED"

    entered = asyncio.Event()
    release = asyncio.Event()
    allowed = True

    class BlockingRetry:
        calls = 0

        async def place_buy(self, request):
            self.calls += 1
            entered.set()
            await release.wait()
            size = request.notional / 0.85
            return ExecutionReport(
                status="FILLED",
                filled_size=size,
                avg_price=0.85,
                fills=[FillPart(price=0.85, size=size, fee=0.0)],
            )

    retry = BlockingRetry()
    manager = stack["manager"]
    manager._executor_provider = lambda _mode: retry
    manager._submission_allowed = lambda _mode: allowed
    manager._retry_after.clear()
    retry_books = books_for(market, up_ask=0.85, down_ask=0.10)
    no_signal = _evaluate(stack, market, books_for(market, up_ask=0.50, down_ask=0.10))

    active = asyncio.create_task(
        manager.handle_event(market, no_signal, retry_books, "paper", None)
    )
    await entered.wait()
    allowed = False
    drained = asyncio.create_task(manager.wait_for_submissions())
    await asyncio.sleep(0)
    assert not drained.done()
    release.set()
    await asyncio.gather(active, drained)

    manager._retry_after.clear()
    await manager.handle_event(market, no_signal, retry_books, "paper", None)
    assert retry.calls == 1
    assert _orders()[0].status == "FILLED"


async def test_unfilled_remainder_expires_with_market():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.85, down_ask=0.10, up_size=2.0)
    await _submit(stack, market, books)
    assert _orders()[0].status == "PARTIALLY_FILLED"

    await stack["manager"].finalize_market(market.market_id, "paper", "market expired")

    order = _orders()[0]
    assert order.status == "EXPIRED"
    assert order.filled_size > 0
    assert "market expired" in order.reason


async def test_paper_mode_never_calls_live_engine():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.95, down_ask=0.05)

    await _submit(stack, market, books)

    assert stack["live_spy"].calls == 0
    assert _orders()[0].mode == "paper"


async def test_paper_report_measures_latency():
    stack = build_stack()
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.90, down_ask=0.10)
    started = time.perf_counter()

    _, results = await _submit(stack, market, books)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert results[0].latency_ms is not None
    assert results[0].latency_ms >= 0
    assert results[0].latency_ms <= max(elapsed_ms, 1.0) + 5.0
    assert _orders()[0].latency_ms is not None


# --- staged entries + retry -------------------------------------------------


async def test_stage1_fires_late_when_ask_reaches_threshold_later():
    stack = build_stack()
    market = make_market(remaining=55)

    _, results = await _submit(stack, market, books_for(market, up_ask=0.60, down_ask=0.40))
    assert results == []
    assert _orders() == []

    # ask only gets to 0.82 later in the window: stage 1 still fires then
    _, results = await _submit(stack, market, books_for(market, up_ask=0.82, down_ask=0.18))
    assert [r.status for r in results] == ["FILLED"]
    assert len(_orders()) == 1


async def test_stage2_and_stage3_add_one_order_each():
    stack = build_stack(make_config(max_open_positions=1))
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.93, down_ask=0.07)

    # 55s left: only stage 1
    await _submit(stack, market, books, make_ctx(remaining=55))
    assert len(_orders()) == 1

    # 45s left: stage 2 (ask >= 0.90) adds one order on the same side
    market = make_market(remaining=45)
    books = books_for(market, up_ask=0.93, down_ask=0.07)
    ctx = make_ctx(
        remaining=45, open_positions=1, exposed_sides=frozenset({"UP"}), stage1_bought=True, stage1_remaining=55
    )
    await _submit(stack, market, books, ctx)
    assert len(_orders()) == 2

    # repeated events at 45s never create a second stage-2 order
    await _submit(stack, market, books, ctx)
    assert len(_orders()) == 2

    # 25s left: stage 3 adds the last order
    market = make_market(remaining=25)
    books = books_for(market, up_ask=0.93, down_ask=0.07)
    ctx = make_ctx(
        remaining=25, open_positions=1, exposed_sides=frozenset({"UP"}), stage1_bought=True, stage1_remaining=55
    )
    await _submit(stack, market, books, ctx)
    await _submit(stack, market, books, ctx)
    orders = _orders()
    assert len(orders) == 3
    assert {o.side for o in orders} == {"UP"}
    assert all(o.status == "FILLED" for o in orders)


async def test_stage2_needs_090_not_just_080():
    stack = build_stack()
    market = make_market(remaining=55)
    await _submit(stack, market, books_for(market, up_ask=0.85, down_ask=0.15))
    assert len(_orders()) == 1  # stage 1

    market = make_market(remaining=45)
    await _submit(stack, market, books_for(market, up_ask=0.85, down_ask=0.15))
    assert len(_orders()) == 1  # 0.85 < 0.90: no stage 2


async def test_blocked_trigger_is_retried_on_next_event():
    stack = build_stack()
    market = make_market(remaining=55)

    # stale book -> blocked, no order
    _, results = await _submit(
        stack, market, books_for(market, up_ask=0.85, down_ask=0.15, up_age_ms=5000)
    )
    assert results[0].status == "BLOCKED"
    assert _orders() == []

    # fresh book on the next event -> same stage is tried again and fills
    _, results = await _submit(stack, market, books_for(market, up_ask=0.85, down_ask=0.15))
    assert results[0].status == "FILLED"
    assert len(_orders()) == 1


async def test_failed_order_is_retried_on_next_event():
    stack = build_stack()
    market = make_market(remaining=55)
    real_provider = stack["manager"]._executor_provider
    calls = {"n": 0}

    class FailOnce:
        async def place_buy(self, request):
            calls["n"] += 1
            if calls["n"] == 1:
                return ExecutionReport(status="CANCELED", error="FOK order was not filled")
            return await real_provider("paper").place_buy(request)

    stack["manager"]._executor_provider = lambda _mode: FailOnce()
    stack["manager"]._retry_gap_ms = 0
    books = books_for(market, up_ask=0.85, down_ask=0.15)

    _, first = await _submit(stack, market, books)
    assert first[0].status == "CANCELED"

    _, second = await _submit(stack, market, books)
    assert second[0].status == "FILLED"
    assert calls["n"] == 2
    assert len(_orders()) == 1  # same row reused, not a second trade


async def test_failed_entry_has_cooldown_and_attempt_cap():
    stack = build_stack()
    manager = stack["manager"]
    market = make_market(remaining=55)
    calls = {"n": 0}

    class AlwaysCancel:
        async def place_buy(self, request):
            calls["n"] += 1
            return ExecutionReport(status="CANCELED", error="FOK order was not filled")

    manager._executor_provider = lambda _mode: AlwaysCancel()
    books = books_for(market, up_ask=0.85, down_ask=0.15)

    # default 1s gap: an immediate second event is held back, not sent to the exchange
    await _submit(stack, market, books)
    _, results = await _submit(stack, market, books)
    assert calls["n"] == 1
    assert results[0].reason == "retry_cooldown"

    # without the gap, attempts stop at the cap
    manager._retry_gap_ms = 0
    for _ in range(10):
        await _submit(stack, market, books)
    assert calls["n"] == manager._max_attempts
    assert len(_orders()) == 1


async def test_price_gap_blocks_order_until_btc_is_far_enough_from_price_to_beat():
    stack = build_stack(make_config(min_price_gap_usd=20.0))
    market = make_market(remaining=55)
    books = books_for(market, up_ask=0.85, down_ask=0.10)

    weak = make_ctx(remaining=55, btc_price=100_005.0, price_to_beat=100_000.0)
    _, results = await _submit(stack, market, books, ctx=weak)
    assert results[0].reason == "price_gap_too_small"
    assert _orders() == []

    # same market, same book: the next event buys as soon as BTC has moved far enough
    strong = make_ctx(remaining=55, btc_price=100_025.0, price_to_beat=100_000.0)
    _, results = await _submit(stack, market, books, ctx=strong)
    assert results[0].status == "FILLED"
    assert len(_orders()) == 1


async def test_chain_gap_met_in_last_30s_buys_only_stage3():
    # gap rule blocks until 25s left: stage 1 missed its 30s deadline and stage 2 needs stage 1,
    # but stage 3 is independent and buys as soon as its rules are met
    stack = build_stack(make_config(min_price_gap_usd=20.0, stage3_min_price_gap_usd=30.0))
    market = make_market(remaining=25)
    books = books_for(market, up_ask=0.93, down_ask=0.05)
    weak = make_ctx(remaining=25, btc_price=100_005.0, price_to_beat=100_000.0)
    _, results = await _submit(stack, market, books, ctx=weak)
    assert _orders() == []
    strong = make_ctx(remaining=25, btc_price=100_030.0, price_to_beat=100_000.0)
    _, results = await _submit(stack, market, books, ctx=strong)
    reasons = sorted(r.reason for r in results)
    assert reasons == ["stage1_missed", "stage1_not_bought", "submitted"]
    orders = _orders()
    assert len(orders) == 1
    assert orders[0].order_key.endswith(":s3")


async def test_chain_full_sequence_three_buys_in_one_market():
    from .helpers import stage_books

    config = make_config(
        min_price_gap_usd=0.0, trade_amount_usdc=1.0, stale_data_max_age_ms=60_000
    )
    stack = build_stack(config)
    manager = stack["manager"]
    market = make_market(remaining=58)
    now = int(time.time() * 1000)

    async def step(offset_s: float, ask: float, **ctx_kwargs):
        books = books_for(market, up_ask=ask, down_ask=round(1 - ask - 0.02, 2))
        stage_books(stack, market, books)
        evaluation = stack["strategy"].evaluate(market, books, now + int(offset_s * 1000), config)
        ctx = make_ctx(remaining=58 - offset_s, balance=10.0, **ctx_kwargs)
        return await manager.handle_event(market, evaluation, books, "paper", ctx)

    # 58s left: stage 1 buys
    results = await step(0, 0.85)
    assert [r.status for r in results] == ["FILLED"]
    s1 = manager.stage1_remaining("paper", market.market_id)
    assert s1 == pytest.approx(58, abs=2)

    held = dict(open_positions=1, exposed_sides=frozenset({"UP"}), stage1_bought=True, stage1_remaining=s1)
    # 45s left: stage 1 bought before the 50s mark -> stage 2 buys (stage 1 itself is a duplicate)
    results = await step(13, 0.93, **held)
    assert sorted(r.status for r in results) == ["BLOCKED", "FILLED"]
    # 28s left: stage 3 buys
    results = await step(30, 0.94, **held)
    assert [r.status for r in results if r.status == "FILLED"] == ["FILLED"]
    assert len(_orders()) == 3


async def test_chain_stage1_late_blocks_stage2_but_not_stage3():
    from .helpers import stage_books

    config = make_config(
        min_price_gap_usd=0.0, trade_amount_usdc=1.0, stale_data_max_age_ms=60_000
    )
    stack = build_stack(config)
    manager = stack["manager"]
    market = make_market(remaining=58)
    now = int(time.time() * 1000)

    async def step(offset_s: float, ask: float, **ctx_kwargs):
        books = books_for(market, up_ask=ask, down_ask=round(1 - ask - 0.02, 2))
        stage_books(stack, market, books)
        evaluation = stack["strategy"].evaluate(market, books, now + int(offset_s * 1000), config)
        ctx = make_ctx(remaining=58 - offset_s, balance=10.0, **ctx_kwargs)
        return await manager.handle_event(market, evaluation, books, "paper", ctx)

    # ask only reaches the trigger at 40s left (rule not met earlier): stage 1 buys late,
    # stage 2 must NOT fire in the same pass, stage 3 waits for the 30s mark
    results = await step(18, 0.93)
    assert sorted(r.reason for r in results) == ["stage1_too_late", "submitted"]
    assert len(_orders()) == 1
    s1 = manager.stage1_remaining("paper", market.market_id)
    assert s1 == pytest.approx(40, abs=2)

    held = dict(open_positions=1, exposed_sides=frozenset({"UP"}), stage1_bought=True, stage1_remaining=s1)
    results = await step(24, 0.93, **held)  # 34s left: stage 2 still refused
    assert "stage1_too_late" in [r.reason for r in results]
    results = await step(30, 0.94, **held)  # 28s left: stage 3 buys
    assert any(r.status == "FILLED" for r in results)
    assert len(_orders()) == 2


async def _timeline(stack, market, config, steps):
    """Replay (seconds_into_window_offset, ask, btc_move) steps through the manager like the bot does."""
    from .helpers import stage_books

    manager = stack["manager"]
    now = int(time.time() * 1000)
    start_remaining = market.remaining_seconds()
    for offset, ask, move in steps:
        books = books_for(market, up_ask=ask, down_ask=round(max(0.01, 1 - ask - 0.02), 2))
        stage_books(stack, market, books)
        evaluation = stack["strategy"].evaluate(market, books, now + int(offset * 1000), config)
        orders = _orders()
        stage1 = [o for o in orders if o.order_key.count(":s") == 0]
        ctx = make_ctx(
            remaining=start_remaining - offset,
            balance=10.0,
            open_positions=1 if orders else 0,
            exposed_sides=frozenset({"UP"}) if orders else frozenset(),
            btc_price=100_000.0 + move,
            price_to_beat=100_000.0,
            stage1_bought=bool(stage1),
            stage1_remaining=manager.stage1_remaining("paper", market.market_id),
        )
        await manager.handle_event(market, evaluation, books, "paper", ctx)


def _keys() -> list[str]:
    return sorted(o.order_key.rsplit(":", 1)[-1] if ":s" in o.order_key else "s1" for o in _orders())


def _timeline_config():
    return make_config(
        final_window_seconds=80, min_price_gap_usd=20.0, stage2_min_price_gap_usd=25.0,
        stage3_min_price_gap_usd=30.0, trade_amount_usdc=1.0,
        stale_data_max_age_ms=60_000, max_open_positions=1,
    )


async def test_timeline_gap_met_early_gives_three_buys():
    config = _timeline_config()
    stack = build_stack(config)
    market = make_market(remaining=78)
    await _timeline(stack, market, config, [
        (0, 0.85, 5),     # 78s: ask ok, gap 5 < 20 -> blocked
        (8, 0.86, 25),    # 70s: gap met -> stage 1 buys right away
        (30, 0.93, 25),   # 48s: stage 2
        (52, 0.95, 30),   # 26s: stage 3 needs gap 30
    ])
    assert _keys() == ["s1", "s2", "s3"]


async def test_timeline_stage1_late_skips_stage2_but_stage3_buys():
    config = _timeline_config()
    stack = build_stack(config)
    market = make_market(remaining=78)
    await _timeline(stack, market, config, [
        (0, 0.93, 3),
        (30, 0.93, 3),    # 48s: gap not met -> nothing
        (38, 0.93, 30),   # 40s: gap met -> stage 1 (late), stage 2 refused
        (44, 0.93, 30),   # 34s: still no stage 2
        (52, 0.95, 30),   # 26s: stage 3
    ])
    assert _keys() == ["s1", "s3"]


async def test_timeline_gap_only_met_in_last_30s_gives_stage3_only():
    config = _timeline_config()
    stack = build_stack(config)
    market = make_market(remaining=78)
    await _timeline(stack, market, config, [(0, 0.93, 3), (40, 0.93, 3), (54, 0.95, 30)])
    assert _keys() == ["s3"]


async def test_timeline_gap_never_met_gives_no_buy():
    config = _timeline_config()
    stack = build_stack(config)
    market = make_market(remaining=78)
    await _timeline(stack, market, config, [(0, 0.93, 3), (30, 0.93, 3), (54, 0.95, 3)])
    assert _orders() == []


async def test_timeline_gap_lost_then_regained_buys_stage2_when_it_returns():
    config = _timeline_config()
    stack = build_stack(config)
    market = make_market(remaining=78)
    await _timeline(stack, market, config, [
        (5, 0.85, 25),    # 73s: stage 1 buys
        (32, 0.93, 4),    # 46s: gap lost -> stage 2 blocked
        (36, 0.93, 28),   # 42s: gap regained -> stage 2 buys at once
    ])
    assert _keys() == ["s1", "s2"]
