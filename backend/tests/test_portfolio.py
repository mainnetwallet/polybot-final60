from __future__ import annotations

from app.database.models import Order, Position
from app.database.session import session_scope
from app.execution.base import ExecutionReport, FillPart
from app.portfolio.service import Portfolio

from .helpers import build_stack


def _order(session, mode: str, market_id: str, side: str = "UP") -> Order:
    order = Order(
        order_key=f"{mode}:{market_id}:{side}",
        client_order_id="client-1",
        mode=mode,
        market_id=market_id,
        side=side,
        token_id="token",
        status="FILLED",
        requested_notional=4.0,
        requested_price=0.80,
    )
    session.add(order)
    session.flush()
    return order


def _fill(portfolio: Portfolio, session, mode: str, market_id: str, side: str = "UP"):
    order = _order(session, mode, market_id, side)
    report = ExecutionReport(
        status="FILLED",
        filled_size=5.0,
        avg_price=0.80,
        fee=0.05,
        exchange_order_id="paper-1",
        fills=[FillPart(price=0.80, size=5.0, fee=0.05, exchange_trade_id="trade-1")],
    )
    portfolio.apply_report(session, mode, order, report)


def test_paper_settlement_win():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        _fill(portfolio, session, "paper", "market-win")
        assert portfolio.balance(session, "paper") == 10.0 - 4.0 - 0.05

        closed = portfolio.settle(session, "paper", "market-win", "UP")

        assert len(closed) == 1
        assert closed[0].status == "closed"
        assert closed[0].realized_pnl == 0.95
        assert portfolio.balance(session, "paper") == 10.95
        assert portfolio.realized_today(session, "paper") == 0.95


def test_paper_settlement_loss():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        _fill(portfolio, session, "paper", "market-loss")
        closed = portfolio.settle(session, "paper", "market-loss", "DOWN")

        assert closed[0].realized_pnl == -4.05
        assert portfolio.balance(session, "paper") == 5.95


def test_paper_and_live_are_never_mixed():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        _fill(portfolio, session, "paper", "market-paper")
        session.add(
            Position(
                mode="live",
                market_id="market-live",
                side="UP",
                size=10.0,
                avg_price=0.8,
                cost_basis=8.0,
                fees=0.0,
                status="open",
            )
        )
        portfolio.settle(session, "paper", "market-paper", "UP")

        live_positions = portfolio.open_positions(session, "live")
        assert len(live_positions) == 1
        assert live_positions[0].market_id == "market-live"
        assert portfolio.balance(session, "live") == 0.0


def test_reset_paper_clears_history_only_for_paper():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        _fill(portfolio, session, "paper", "market-x")
        session.add(
            Position(mode="live", market_id="market-live", side="DOWN", size=1.0, status="open")
        )
        portfolio.reset_paper(session, 10.0)

        assert portfolio.open_positions(session, "paper") == []
        assert portfolio.balance(session, "paper") == 10.0
        assert len(portfolio.open_positions(session, "live")) == 1
