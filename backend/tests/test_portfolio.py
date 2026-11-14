from __future__ import annotations

from datetime import datetime, timezone

from app.database.models import Order, Position
from app.database.session import session_scope
from app.execution.base import ExecutionReport, FillPart
from app.portfolio.service import Portfolio, start_of_day_utc

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


def test_start_of_day_utc_normalizes_aware_input():
    local = datetime(2026, 11, 12, 1, 30, tzinfo=timezone.utc)
    assert start_of_day_utc(local) == datetime(2026, 11, 12, 0, 0)


def test_unrealized_pnl_calculation_does_not_write_to_position():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        position = Position(
            mode="paper",
            market_id="marked-market",
            side="UP",
            size=5.0,
            avg_price=0.8,
            cost_basis=4.0,
            fees=0.05,
            unrealized_pnl=0.0,
            status="open",
        )
        session.add(position)
        session.flush()

        marked = portfolio.unrealized_pnl(
            session, "paper", {("marked-market", "UP"): 0.9}
        )

        assert abs(marked - 0.45) < 1e-9
        assert position.unrealized_pnl == 0.0


def test_daily_reset_zeroes_today_without_deleting_history():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        _fill(portfolio, session, "paper", "market-daily")
        portfolio.settle(session, "paper", "market-daily", "DOWN")  # loss
        assert portfolio.trades_today(session, "paper") == 1
        assert portfolio.realized_today(session, "paper") < 0

    with session_scope() as session:
        portfolio.reset_daily(session, "paper")

    with session_scope() as session:
        assert portfolio.trades_today(session, "paper") == 0
        assert portfolio.realized_today(session, "paper") == 0
        # history and balance are untouched
        assert session.query(Order).count() == 1
        assert session.query(Position).count() == 1
        assert portfolio.balance(session, "paper") < 10.0


def test_reset_all_wipes_every_table_and_recreates_accounts():
    from app.database.models import Account, AppState, BotSetting, Fill, LogEntry

    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        _fill(portfolio, session, "paper", "market-wipe")
        session.add(LogEntry(level="INFO", category="system", message="x"))
        session.add(BotSetting(key="trigger_threshold", value=0.9))
        portfolio.reset_daily(session, "paper")

    with session_scope() as session:
        portfolio.reset_all(session)

    with session_scope() as session:
        assert session.query(Order).count() == 0
        assert session.query(Fill).count() == 0
        assert session.query(Position).count() == 0
        assert session.query(LogEntry).count() == 0
        assert session.query(BotSetting).count() == 0
        assert session.query(Account).count() == 2
        assert session.get(AppState, 1).status == "STOPPED"
        assert portfolio.balance(session, "paper") == 10.0


def test_snapshot_unrealized_keeps_pnl_for_settling_position():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        _fill(portfolio, session, "paper", "market-ended")  # 5 sh @ 0.80, fee 0.05
        # market already dropped from tracking: no live marks, only the stored value
        assert portfolio.unrealized_pnl(session, "paper", {}) == 0.0

        portfolio.snapshot_unrealized(
            session, "paper", "market-ended", {("market-ended", "UP"): 0.99}
        )

        # 5 * 0.99 - 4.0 - 0.05 = 0.90
        assert portfolio.unrealized_pnl(session, "paper", {}) == 0.9
        # other markets are untouched
        _fill(portfolio, session, "paper", "market-other", "DOWN")
        portfolio.snapshot_unrealized(
            session, "paper", "market-ended", {("market-ended", "UP"): 0.50}
        )
        positions = {p.market_id: p for p in portfolio.open_positions(session, "paper")}
        assert positions["market-other"].unrealized_pnl == 0.0


def test_trades_today_ignores_canceled_orders_without_fills():
    stack = build_stack()
    portfolio = stack["portfolio"]
    with session_scope() as session:
        def add(key: str, status: str, filled: float) -> None:
            session.add(
                Order(
                    order_key=key,
                    client_order_id=key,
                    mode="paper",
                    market_id="m",
                    side="UP",
                    token_id="token",
                    status=status,
                    requested_notional=4.0,
                    requested_price=0.8,
                    filled_size=filled,
                )
            )

        add("paper:m1:UP", "CANCELED", 0.0)   # FOK not filled -> not a trade
        add("paper:m2:UP", "EXPIRED", 0.0)    # expired, no fill -> not a trade
        add("paper:m3:UP", "CANCELED", 2.0)   # IOC partial fill then canceled -> real trade
        add("paper:m4:UP", "FILLED", 5.0)     # real trade
        session.flush()

        assert portfolio.trades_today(session, "paper") == 2
