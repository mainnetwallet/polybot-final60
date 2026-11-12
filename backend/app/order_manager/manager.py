from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config.runtime import SettingsService
from app.config.settings import Settings
from app.database.models import Order, Position, TriggerEvent, utcnow
from app.database.session import session_scope
from app.execution.base import ExecutionEngine, ExecutionReport, FillPart, OrderRequest
from app.market_data.book import OrderBook
from app.market_discovery.types import MarketInfo
from app.monitoring.logs import LogService
from app.portfolio.service import Portfolio
from app.risk_manager.manager import RiskContext, RiskManager
from app.strategy.types import Signal, StrategyEvaluation

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("PENDING", "SUBMITTED", "PARTIALLY_FILLED")
TERMINAL_EMPTY = ("REJECTED", "CANCELED", "EXPIRED")
ExecutorProvider = Callable[[str], ExecutionEngine]


@dataclass
class OrderResult:
    order_id: int | None
    side: str
    status: str
    reason: str
    filled_size: float = 0.0
    avg_price: float | None = None
    latency_ms: float | None = None
    exchange_order_id: str | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "side": self.side,
            "status": self.status,
            "reason": self.reason,
            "filled_size": self.filled_size,
            "avg_price": self.avg_price,
            "latency_ms": self.latency_ms,
            "exchange_order_id": self.exchange_order_id,
            "error": self.error,
        }


class OrderManager:
    def __init__(
        self,
        settings: Settings,
        runtime: SettingsService,
        portfolio: Portfolio,
        risk: RiskManager,
        executor_provider: ExecutorProvider,
        logs: LogService,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._portfolio = portfolio
        self._risk = risk
        self._executor_provider = executor_provider
        self._logs = logs
        self._lock = asyncio.Lock()
        self._trigger_seen: set[tuple[str, str, str]] = set()
        self._trigger_market: str | None = None
        self._retry_after: dict[int, float] = {}

    # ------------------------------------------------------------------
    def _choose(self, signals: list[Signal], policy: str) -> Signal | None:
        if not signals:
            return None
        if policy == "block_both" and len(signals) > 1:
            return None
        if policy == "down_first":
            order = {"DOWN": 0, "UP": 1}
        elif policy == "highest_ask":
            best = max(signals, key=lambda s: (s.best_ask, 1 if s.side == "UP" else 0))
            return best
        else:  # up_first default
            order = {"UP": 0, "DOWN": 1}
        return sorted(signals, key=lambda s: order.get(s.side, 9))[0]

    def _order_key(self, mode: str, signal: Signal, policy: str) -> str:
        if policy == "one_per_side":
            return f"{mode}:{signal.market_id}:{signal.side}"
        if policy == "allow":
            return f"{mode}:{signal.market_id}:{signal.side}:{signal.observed_at_ms // 1000}"
        return f"{mode}:{signal.market_id}"

    def _log_trigger_once(
        self, signal: Signal, decision: str, reason: str, mode: str, persist: bool = True
    ) -> None:
        if self._trigger_market != signal.market_id:
            self._trigger_market = signal.market_id
            self._trigger_seen.clear()
        key = (signal.market_id, signal.side, decision)
        if key in self._trigger_seen:
            return
        self._trigger_seen.add(key)
        with session_scope() as session:
            session.add(
                TriggerEvent(
                    market_id=signal.market_id,
                    side=signal.side,
                    best_ask=signal.best_ask,
                    ask_size=signal.ask_size,
                    remaining_seconds=signal.remaining_seconds,
                    threshold=signal.threshold,
                    triggered=True,
                    decision=decision,
                    reason=reason,
                )
            )
        self._logs.log(
            "INFO" if decision == "submitted" else "WARNING",
            "trigger",
            f"{signal.side} ask {signal.best_ask:.3f} >= {signal.threshold:.2f} -> {decision}",
            payload={
                "market_id": signal.market_id,
                "side": signal.side,
                "best_ask": signal.best_ask,
                "ask_size": signal.ask_size,
                "remaining_seconds": signal.remaining_seconds,
                "decision": decision,
                "reason": reason,
            },
            mode=mode,
            persist=persist,
        )

    # ------------------------------------------------------------------
    async def handle_event(
        self,
        market: MarketInfo,
        evaluation: StrategyEvaluation,
        books: dict[str, OrderBook],
        mode: str,
        ctx: RiskContext,
    ) -> list[OrderResult]:
        config = self._runtime.get()

        if mode == "paper":
            await self._retry_partial_orders(market, books, mode, config)

        if not evaluation.signals:
            return []

        chosen = self._choose(evaluation.signals, config.conflict_policy)
        results: list[OrderResult] = []

        async with self._lock:
            for signal in evaluation.signals:
                if chosen is None:
                    self._log_trigger_once(signal, "blocked_conflict", "both sides qualified; policy block_both", mode)
                    results.append(OrderResult(None, signal.side, "BLOCKED", "conflict_blocked"))
                    continue
                if signal is not chosen:
                    self._log_trigger_once(signal, "skipped_conflict", f"conflict policy {config.conflict_policy}", mode)
                    results.append(OrderResult(None, signal.side, "SKIPPED", "conflict_not_selected"))
                    continue
                result = await self._submit(market, signal, books, mode, ctx, config)
                results.append(result)
        return results

    async def _submit(
        self,
        market: MarketInfo,
        signal: Signal,
        books: dict[str, OrderBook],
        mode: str,
        ctx: RiskContext,
        config,
    ) -> OrderResult:
        if market.remaining_seconds() <= 0:
            self._log_trigger_once(signal, "blocked_expired", "market expired before submission", mode)
            return OrderResult(None, signal.side, "BLOCKED", "market_expired")

        key = self._order_key(mode, signal, config.duplicate_policy)
        with session_scope() as session:
            blocker = self._find_duplicate(session, mode, signal, config.duplicate_policy)
            if blocker is not None:
                reason = f"duplicate blocked by order #{blocker.id} ({blocker.status})"
                self._log_trigger_once(signal, "blocked_duplicate", reason, mode)
                return OrderResult(blocker.id, signal.side, "BLOCKED", "duplicate")

        decision = self._risk.check(signal, ctx, config)
        if not decision.approved:
            self._log_trigger_once(signal, f"blocked_{decision.reason}", decision.detail, mode)
            return OrderResult(None, signal.side, "BLOCKED", decision.reason)

        order_id, reused = self._create_order(mode, market, signal, key, config)
        if order_id is None:
            self._log_trigger_once(signal, "blocked_duplicate", "idempotency key already active", mode)
            return OrderResult(None, signal.side, "BLOCKED", "duplicate")

        request = OrderRequest(
            order_key=key,
            client_order_id=f"ord-{uuid.uuid4().hex[:20]}",
            market_id=market.market_id,
            side=signal.side,
            token_id=signal.token_id,
            notional=config.trade_amount_usdc,
            max_price=config.max_execution_price,
            observed_at_ms=signal.observed_at_ms,
            order_min_size=market.min_order_size,
        )

        self._logs.log(
            "INFO",
            "order_submit",
            f"marketable BUY {signal.side} {config.trade_amount_usdc:.2f} USDC at ask {signal.best_ask:.3f}",
            payload={
                "market_id": market.market_id,
                "side": signal.side,
                "best_ask": signal.best_ask,
                "notional": config.trade_amount_usdc,
                "max_price": config.max_execution_price,
                "mode": mode,
                "order_key": key,
            },
            mode=mode,
        )

        report: ExecutionReport
        try:
            executor = self._executor_provider(mode)
            report = await executor.place_buy(request)
        except Exception as exc:
            logger.exception("executor raised")
            report = ExecutionReport(status="REJECTED", error=f"{type(exc).__name__}: {exc}")

        return self._finalize_order(order_id, report, signal, mode)

    def _find_duplicate(self, session, mode: str, signal: Signal, policy: str) -> Order | None:
        query = select(Order).where(Order.mode == mode, Order.market_id == signal.market_id)
        if policy in {"one_per_market", "one_per_side"}:
            if policy == "one_per_side":
                query = query.where(Order.side == signal.side)
            orders = session.execute(query).scalars().all()
            for order in orders:
                if order.status in ACTIVE_STATUSES or order.status == "FILLED" or order.filled_size > 0:
                    return order
            return None
        orders = session.execute(query.where(Order.status.in_(ACTIVE_STATUSES))).scalars().all()
        return orders[0] if orders else None

    def _create_order(
        self, mode: str, market: MarketInfo, signal: Signal, key: str, config
    ) -> tuple[int | None, bool]:
        try:
            with session_scope() as session:
                existing = session.execute(
                    select(Order).where(Order.order_key == key)
                ).scalar_one_or_none()
                if existing is not None:
                    if existing.status in ACTIVE_STATUSES or existing.filled_size > 0:
                        return None, True
                    existing.status = "PENDING"
                    existing.requested_price = signal.best_ask
                    existing.submitted_price = None
                    existing.error = None
                    existing.reason = ""
                    existing.latency_ms = None
                    session.flush()
                    return existing.id, True
                order = Order(
                    order_key=key,
                    client_order_id=f"ord-{uuid.uuid4().hex[:20]}",
                    mode=mode,
                    market_id=market.market_id,
                    side=signal.side,
                    token_id=signal.token_id,
                    status="PENDING",
                    requested_notional=config.trade_amount_usdc,
                    requested_price=signal.best_ask,
                )
                session.add(order)
                session.flush()
                return order.id, False
        except IntegrityError:
            return None, True

    def _finalize_order(
        self, order_id: int, report: ExecutionReport, signal: Signal, mode: str
    ) -> OrderResult:
        with session_scope() as session:
            order = session.get(Order, order_id)
            if order is None:
                return OrderResult(None, signal.side, "REJECTED", "missing_order")
            previous_size = order.filled_size
            previous_cost = (order.avg_fill_price or 0.0) * previous_size
            order.status = report.status
            order.filled_size = round(previous_size + report.filled_size, 10)
            if report.filled_size > 0 and report.avg_price:
                new_cost = previous_cost + report.filled_size * report.avg_price
                order.avg_fill_price = (
                    round(new_cost / order.filled_size, 8) if order.filled_size > 0 else None
                )
            order.fee = round(order.fee + report.fee, 10)
            order.submitted_price = report.submitted_price or order.submitted_price
            order.exchange_order_id = report.exchange_order_id or order.exchange_order_id
            if order.latency_ms is None:
                order.latency_ms = report.latency_ms
            order.error = report.error
            order.reason = signal.reason if hasattr(signal, "reason") else ""

            self._portfolio.apply_report(session, mode, order, report)
            result = OrderResult(
                order.id,
                signal.side,
                order.status,
                "submitted",
                order.filled_size,
                order.avg_fill_price,
                order.latency_ms,
                order.exchange_order_id,
                order.error,
            )
            session.flush()

        decision = "submitted" if result.status in {"SUBMITTED", "PARTIALLY_FILLED", "FILLED"} else f"failed_{result.status.lower()}"
        self._log_trigger_once(signal, decision, f"order {result.status}", mode)
        if report.fills:
            self._logs.log(
                "INFO",
                "fill",
                f"filled {report.filled_size:.4f} shares @ {report.avg_price or 0:.4f}",
                payload={
                    "order_id": result.order_id,
                    "filled_size": result.filled_size,
                    "avg_price": result.avg_price,
                    "latency_ms": result.latency_ms,
                    "exchange_order_id": result.exchange_order_id,
                },
                mode=mode,
            )
        if report.error or report.status == "REJECTED":
            self._logs.log(
                "ERROR",
                "error",
                f"order {report.status}: {report.error or 'rejected'}",
                payload={"order_id": result.order_id, "raw": report.raw},
                mode=mode,
            )
        return result

    # ------------------------------------------------------------------
    async def _retry_partial_orders(
        self, market: MarketInfo, books: dict[str, OrderBook], mode: str, config
    ) -> None:
        if market.remaining_seconds() <= 0:
            return
        with session_scope() as session:
            orders = session.execute(
                select(Order).where(
                    Order.mode == mode,
                    Order.market_id == market.market_id,
                    Order.status == "PARTIALLY_FILLED",
                )
            ).scalars().all()
            pending = [
                (o.id, o.side, o.filled_size, o.avg_fill_price or 0.0, o.requested_notional)
                for o in orders
            ]

        for order_id, side, filled_size, avg_fill_price, requested_notional in pending:
            now_ms = int(time.time() * 1000)
            last = self._retry_after.get(order_id, 0.0)
            if now_ms - last < 500:
                continue
            book = books.get(market.token_for(side))
            if book is None or book.best_ask is None or book.ask_size is None or book.ask_size <= 0:
                continue
            if book.best_ask > config.max_execution_price + 1e-9:
                continue
            spent = filled_size * avg_fill_price
            remaining = requested_notional - spent
            if remaining <= 0.01:
                continue
            self._retry_after[order_id] = now_ms
            request = OrderRequest(
                order_key=f"retry:{order_id}",
                client_order_id=f"retry-{uuid.uuid4().hex[:16]}",
                market_id=market.market_id,
                side=side,
                token_id=market.token_for(side),
                notional=round(remaining, 8),
                max_price=config.max_execution_price,
                observed_at_ms=now_ms,
                order_min_size=market.min_order_size,
            )
            try:
                report = await self._executor_provider(mode).place_buy(request)
            except Exception as exc:
                logger.warning("partial retry failed: %s", exc)
                continue
            if not report.fills:
                continue
            with session_scope() as session:
                order = session.get(Order, order_id)
                if order is None or order.status != "PARTIALLY_FILLED":
                    continue
                previous_size = order.filled_size
                previous_cost = (order.avg_fill_price or 0.0) * previous_size
                order.filled_size = round(previous_size + report.filled_size, 10)
                if report.avg_price and order.filled_size > 0:
                    order.avg_fill_price = round(
                        (previous_cost + report.filled_size * report.avg_price) / order.filled_size, 8
                    )
                order.fee = round(order.fee + report.fee, 10)
                order.status = report.status
                self._portfolio.apply_report(session, mode, order, report)
            self._logs.log(
                "INFO",
                "fill",
                f"partial retry filled {report.filled_size:.4f} @ {report.avg_price or 0:.4f}",
                payload={"order_id": order_id, "side": side, "status": report.status},
                mode=mode,
            )

    # ------------------------------------------------------------------
    async def finalize_market(self, market_id: str, mode: str, reason: str = "market expired") -> int:
        if mode == "live":
            try:
                await self._executor_provider("live").cancel_open_orders()
            except Exception as exc:
                logger.warning("live cancel on finalize failed: %s", exc)
        closed = 0
        with session_scope() as session:
            orders = session.execute(
                select(Order).where(
                    Order.mode == mode,
                    Order.market_id == market_id,
                    Order.status.in_(ACTIVE_STATUSES),
                )
            ).scalars().all()
            for order in orders:
                order.status = "EXPIRED"
                order.reason = reason
                order.error = order.error or None
                closed += 1
        if closed:
            self._logs.log(
                "WARNING",
                "order",
                f"{closed} open order(s) expired: {reason}",
                payload={"market_id": market_id, "count": closed},
                mode=mode,
            )
        self._retry_after.clear()
        return closed

    async def reconcile_live_order(self, order_id: int) -> None:
        with session_scope() as session:
            order = session.get(Order, order_id)
            if order is None or order.mode != "live" or not order.exchange_order_id:
                return
            exchange_id = order.exchange_order_id
            current_size = order.filled_size
            status = order.status
            price = order.avg_fill_price or order.requested_price

        engine = self._executor_provider("live")
        payload = await getattr(engine, "fetch_order", lambda _id: None)(exchange_id)
        if not isinstance(payload, dict):
            return
        try:
            size_matched = float(payload.get("size_matched") or payload.get("sizeMatched") or 0)
        except (TypeError, ValueError):
            size_matched = 0.0
        raw_status = str(payload.get("status") or "").upper()

        delta = round(size_matched - current_size, 10)
        if delta <= 0 and raw_status not in {"CANCELED", "CANCELLED", "EXPIRED", "MATCHED", "FILLED"}:
            return

        report = ExecutionReport(
            status="FILLED" if raw_status in {"MATCHED", "FILLED"} else status,
            filled_size=max(0.0, delta),
            avg_price=price if delta > 0 else None,
            exchange_order_id=exchange_id,
            fills=[
                FillPart(
                    price=price,
                    size=delta,
                    exchange_trade_id=f"live-reconcile-{exchange_id}-{size_matched}",
                )
            ]
            if delta > 0
            else [],
        )
        with session_scope() as session:
            order = session.get(Order, order_id)
            if order is None:
                return
            order.filled_size = round(current_size + max(0.0, delta), 10)
            if raw_status in {"MATCHED", "FILLED"}:
                order.status = "FILLED"
            elif raw_status in {"CANCELED", "CANCELLED", "EXPIRED"}:
                order.status = "EXPIRED" if order.status != "FILLED" else order.status
            self._portfolio.apply_report(session, "live", order, report)
        if delta > 0:
            self._logs.log(
                "INFO",
                "fill",
                f"live reconciliation added {delta:.4f} shares",
                payload={"order_id": order_id, "exchange_order_id": exchange_id},
                mode="live",
            )

    def reset_trigger_cache(self) -> None:
        self._trigger_seen.clear()
        self._trigger_market = None
        self._retry_after.clear()


__all__ = ["OrderManager", "OrderResult", "datetime", "timezone", "Position"]
