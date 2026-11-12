from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Fill, Order, Position


class AnalyticsService:
    def summary(self, session: Session, mode: str, unrealized: float = 0.0) -> dict:
        positions = session.execute(
            select(Position).where(Position.mode == mode)
        ).scalars().all()
        closed = [p for p in positions if p.status == "closed"]
        wins = [p for p in closed if p.realized_pnl > 0]
        realized = round(sum(p.realized_pnl for p in closed), 6)
        total_pnl = round(realized + unrealized, 6)
        weighted_cost = sum(p.cost_basis for p in positions)
        weighted_size = sum(p.size for p in positions)
        avg_entry = round(weighted_cost / weighted_size, 6) if weighted_size > 0 else None

        latency_avg = session.execute(
            select(func.avg(Order.latency_ms)).where(
                Order.mode == mode, Order.latency_ms.is_not(None)
            )
        ).scalar_one()
        latency_last = session.execute(
            select(Order.latency_ms)
            .where(Order.mode == mode, Order.latency_ms.is_not(None))
            .order_by(Order.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        filled_orders = session.execute(
            select(func.count(Order.id)).where(Order.mode == mode, Order.filled_size > 0)
        ).scalar_one()
        fees = session.execute(
            select(func.coalesce(func.sum(Fill.fee), 0.0)).where(Fill.mode == mode)
        ).scalar_one()
        volume = session.execute(
            select(func.coalesce(func.sum(Fill.price * Fill.size), 0.0)).where(Fill.mode == mode)
        ).scalar_one()

        return {
            "mode": mode,
            "total_trades": int(filled_orders),
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len([p for p in closed if p.realized_pnl <= 0]),
            "win_rate": round(len(wins) / len(closed) * 100.0, 2) if closed else None,
            "total_pnl": total_pnl,
            "realized_pnl": realized,
            "unrealized_pnl": round(unrealized, 6),
            "avg_entry_price": avg_entry,
            "avg_latency_ms": round(float(latency_avg), 2) if latency_avg is not None else None,
            "last_latency_ms": round(float(latency_last), 2) if latency_last is not None else None,
            "fees_paid": round(float(fees), 6),
            "volume_usdc": round(float(volume), 6),
        }
