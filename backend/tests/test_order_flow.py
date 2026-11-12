from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.database.models import Order, TriggerEvent
from app.database.session import session_scope

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
    market = make_market(remaining=30)
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
    market = make_market(remaining=25)
    books = books_for(market, up_ask=0.97, down_ask=0.03)

    await _submit(stack, market, books)

    order = _orders()[0]
    # The 0.80 threshold must never appear as an execution or limit price.
    assert order.requested_price == 0.97
    assert order.avg_fill_price >= 0.97
    assert order.submitted_price != 0.80


async def test_duplicate_orders_are_blocked():
    stack = build_stack()
    market = make_market(remaining=30)
    books = books_for(market, up_ask=0.90, down_ask=0.10)

    await _submit(stack, market, books)
    _, results = await _submit(stack, market, books)

    assert len(_orders()) == 1
    assert results[0].status == "BLOCKED"
    assert results[0].reason == "duplicate"
    with session_scope() as session:
        events = session.execute(select(TriggerEvent)).scalars().all()
    assert any(e.decision == "blocked_duplicate" for e in events)


async def test_concurrent_triggers_create_one_order():
    stack = build_stack()
    market = make_market(remaining=30)
    books = books_for(market, up_ask=0.90, down_ask=0.10)
    stack["books"].update(books)
    evaluation = _evaluate(stack, market, books)
    ctx = make_ctx(remaining=market.remaining_seconds())

    await asyncio.gather(
        stack["manager"].handle_event(market, evaluation, books, "paper", ctx),
        stack["manager"].handle_event(market, evaluation, books, "paper", ctx),
    )
    assert len(_orders()) == 1


async def test_conflict_policy_up_first_selects_one_side():
    stack = build_stack()
    market = make_market(remaining=20)
    books = books_for(market, up_ask=0.88, down_ask=0.86)

    _, results = await _submit(stack, market, books)

    orders = _orders()
    assert len(orders) == 1
    assert orders[0].side == "UP"
    assert any(r.reason == "conflict_not_selected" for r in results)


async def test_conflict_policy_block_both_places_no_order():
    stack = build_stack(make_config(conflict_policy="block_both"))
    market = make_market(remaining=20)
    books = books_for(market, up_ask=0.88, down_ask=0.86)

    _, results = await _submit(stack, market, books)

    assert _orders() == []
    assert all(r.status == "BLOCKED" for r in results)


async def test_stale_market_data_blocks_execution():
    stack = build_stack()
    market = make_market(remaining=30)
    books = books_for(market, 0.90, 0.10, up_age_ms=5000)

    evaluation, results = await _submit(stack, market, books)

    assert evaluation.signals, "price qualifies, so the block must come from freshness"
    assert results[0].reason == "stale_data"
    assert _orders() == []
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
    market = make_market(remaining=30)
    books = books_for(market, up_ask=0.995, down_ask=0.005)

    _, results = await _submit(stack, market, books)

    assert results[0].reason == "max_execution_price_exceeded"
    assert _orders() == []


async def test_partial_fill_then_retry_completes():
    stack = build_stack()
    market = make_market(remaining=30)
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


async def test_unfilled_remainder_expires_with_market():
    stack = build_stack()
    market = make_market(remaining=30)
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
    market = make_market(remaining=30)
    books = books_for(market, up_ask=0.95, down_ask=0.05)

    await _submit(stack, market, books)

    assert stack["live_spy"].calls == 0
    assert _orders()[0].mode == "paper"


async def test_paper_report_measures_latency():
    stack = build_stack()
    market = make_market(remaining=30)
    books = books_for(market, up_ask=0.90, down_ask=0.10)
    started = time.perf_counter()

    _, results = await _submit(stack, market, books)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert results[0].latency_ms is not None
    assert results[0].latency_ms >= 0
    assert results[0].latency_ms <= max(elapsed_ms, 1.0) + 5.0
    assert _orders()[0].latency_ms is not None
