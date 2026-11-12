"""Accounts, fills, positions and PnL. Paper and live are never combined."""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.database.models import Account, Fill, Order, Position, utcnow
from app.execution.base import ExecutionReport

logger = logging.getLogger(__name__)


def start_of_day_utc(now: datetime | None = None) -> datetime:
    now = now or utcnow()
    return datetime.combine(now.date(), time.min)


class Portfolio:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    def ensure_accounts(self, session: Session) -> None:
        paper = session.execute(select(Account).where(Account.mode == "paper")).scalar_one_or_none()
        if paper is None:
            session.add(
                Account(
                    mode="paper",
                    balance=self._settings.paper_starting_balance,
                    starting_balance=self._settings.paper_starting_balance,
                )
            )
        live = session.execute(select(Account).where(Account.mode == "live")).scalar_one_or_none()
        if live is None:
            session.add(Account(mode="live", balance=0.0, starting_balance=0.0))
        session.flush()

    def account(self, session: Session, mode: str) -> Account:
        account = session.execute(select(Account).where(Account.mode == mode)).scalar_one_or_none()
        if account is None:
            self.ensure_accounts(session)
            account = session.execute(select(Account).where(Account.mode == mode)).scalar_one()
        return account

    def balance(self, session: Session, mode: str) -> float:
        return float(self.account(session, mode).balance)

    def set_cached_balance(self, session: Session, mode: str, balance: float) -> None:
        if mode == "paper":
            return  # paper balance is authoritative locally
        account = self.account(session, mode)
        account.balance = float(balance)

    # ------------------------------------------------------------------
    def apply_report(
        self, session: Session, mode: str, order: Order, report: ExecutionReport
    ) -> None:
        if not report.fills:
            return
        for part in report.fills:
            session.add(
                Fill(
                    order_id=order.id,
                    mode=mode,
                    market_id=order.market_id,
                    side=order.side,
                    price=part.price,
                    size=part.size,
                    fee=part.fee,
                    exchange_trade_id=part.exchange_trade_id,
                )
            )
        total_size = sum(p.size for p in report.fills)
        total_cost = sum(p.price * p.size for p in report.fills)
        total_fee = sum(p.fee for p in report.fills)

        position = session.execute(
            select(Position).where(
                Position.mode == mode,
                Position.market_id == order.market_id,
                Position.side == order.side,
            )
        ).scalar_one_or_none()
        if position is None:
            position = Position(
                mode=mode,
                market_id=order.market_id,
                side=order.side,
                size=0.0,
                avg_price=0.0,
                cost_basis=0.0,
                fees=0.0,
                status="open",
            )
            session.add(position)
            session.flush()

        position.size = round(position.size + total_size, 10)
        position.cost_basis = round(position.cost_basis + total_cost, 10)
        position.fees = round(position.fees + total_fee, 10)
        position.avg_price = (
            round(position.cost_basis / position.size, 8) if position.size > 0 else 0.0
        )
        position.status = "open"

        if mode == "paper":
            account = self.account(session, "paper")
            account.balance = round(account.balance - total_cost - total_fee, 10)

    # ------------------------------------------------------------------
    def mark_to_market(
        self,
        session: Session,
        mode: str,
        marks: dict[tuple[str, str], float],
        market_id: str | None = None,
    ) -> float:
        """Update stored unrealized PnL. marks keyed by (market_id, side) best bid."""
        positions = session.execute(
            select(Position).where(Position.mode == mode, Position.status == "open")
        ).scalars().all()
        total = 0.0
        for position in positions:
            if market_id is not None and position.market_id != market_id:
                total += position.unrealized_pnl
                continue
            mark = marks.get((position.market_id, position.side))
            if mark is None:
                total += position.unrealized_pnl
                continue
            value = position.size * mark
            position.unrealized_pnl = round(value - position.cost_basis - position.fees, 10)
            total += position.unrealized_pnl
        return round(total, 10)

    def open_positions(self, session: Session, mode: str) -> list[Position]:
        return list(
            session.execute(
                select(Position).where(Position.mode == mode, Position.status == "open")
            ).scalars().all()
        )

    def realized_today(self, session: Session, mode: str) -> float:
        value = session.execute(
            select(func.coalesce(func.sum(Position.realized_pnl), 0.0)).where(
                Position.mode == mode,
                Position.status == "closed",
                Position.closed_at >= start_of_day_utc(),
            )
        ).scalar_one()
        return round(float(value), 10)

    def pnl_summary(self, session: Session, mode: str, unrealized: float) -> dict:
        realized = self.realized_today(session, mode)
        return {
            "realized_pnl": round(realized, 6),
            "unrealized_pnl": round(unrealized, 6),
            "daily_pnl": round(realized + unrealized, 6),
        }

    def trades_today(self, session: Session, mode: str) -> int:
        value = session.execute(
            select(func.count(Order.id)).where(
                Order.mode == mode,
                Order.created_at >= start_of_day_utc(),
                Order.status.in_(["SUBMITTED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED"]),
            )
        ).scalar_one()
        return int(value)

    def settle(self, session: Session, mode: str, market_id: str, outcome: str) -> list[Position]:
        positions = session.execute(
            select(Position).where(
                Position.mode == mode,
                Position.market_id == market_id,
                Position.status == "open",
            )
        ).scalars().all()
        closed: list[Position] = []
        total_payout = 0.0
        for position in positions:
            payout = position.size if position.side == outcome else 0.0
            pnl = round(payout - position.cost_basis - position.fees, 10)
            position.realized_pnl = pnl
            position.unrealized_pnl = 0.0
            position.status = "closed"
            position.resolution = outcome
            position.closed_at = utcnow()
            total_payout += payout
            closed.append(position)
        if mode == "paper" and total_payout:
            account = self.account(session, "paper")
            account.balance = round(account.balance + total_payout, 10)
        return closed

    def reset_paper(self, session: Session, balance: float) -> None:
        session.execute(delete(Fill).where(Fill.mode == "paper"))
        session.execute(delete(Order).where(Order.mode == "paper"))
        session.execute(delete(Position).where(Position.mode == "paper"))
        account = self.account(session, "paper")
        account.balance = float(balance)
        account.starting_balance = float(balance)
        session.flush()
