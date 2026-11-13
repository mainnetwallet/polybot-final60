from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import clock
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


def _book_top_asks(book: OrderBook | None, n: int = 3) -> dict | None:
    """Cheapest ``n`` ask levels plus book age, for failed-order diagnostics."""
    if book is None:
        return None
    levels = [[price, size] for price, size in book.level_prices()[:n]]
    return {"asks": levels, "age_ms": round(book.age_ms()) if book.updated_at_ms else None}
TERMINAL_EMPTY = ("REJECTED", "CANCELED", "EXPIRED")
ExecutorProvider = Callable[[str], ExecutionEngine]
SubmissionAllowed = Callable[[str], bool]
_STAGE_SUFFIX = re.compile(r":s(\d+)$")


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
        submission_allowed: SubmissionAllowed | None = None,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._portfolio = portfolio
        self._risk = risk
        self._executor_provider = executor_provider
        self._logs = logs
        self._submission_allowed = submission_allowed or (lambda _mode: True)
        self._lock = asyncio.Lock()
        self._trigger_seen: set[tuple[str, str, str, int]] = set()
        self._trigger_market: str | None = None
        self._retry_after: dict[int, float] = {}
        self._max_attempts = settings.entry_max_attempts
        self._retry_gap_ms = settings.entry_retry_gap_ms
        self._attempts: dict[str, int] = {}
        self._last_attempt_ms: dict[str, int] = {}
        # (mode, market) -> seconds remaining when stage 1 last got an accepted order (stage chain)
        self._stage1_remaining: dict[tuple[str, str], float] = {}
        self._background_writes: set[asyncio.Task] = set()

    def stage1_remaining(self, mode: str, market_id: str) -> float | None:
        return self._stage1_remaining.get((mode, market_id))

    def _schedule_trigger(self, signal: Signal, decision: str, reason: str, mode: str) -> None:
        async def persist_then_publish() -> None:
            await asyncio.to_thread(self._persist_trigger, signal, decision, reason)
            self._logs.log(
                "INFO" if decision == "submitted" else "WARNING",
                "trigger",
                f"{'S' + str(signal.stage) + ' ' if signal.stage > 1 else ''}"
                f"{signal.side} ask {signal.best_ask:.3f} >= {signal.threshold:.2f} -> {decision}",
                payload={
                    "market_id": signal.market_id,
                    "side": signal.side,
                    "stage": signal.stage,
                    "best_ask": signal.best_ask,
                    "ask_size": signal.ask_size,
                    "remaining_seconds": signal.remaining_seconds,
                    "decision": decision,
                    "reason": reason,
                },
                mode=mode,
            )

        task = asyncio.create_task(persist_then_publish())
        self._background_writes.add(task)
        task.add_done_callback(self._background_writes.discard)

    async def close(self) -> None:
        if self._background_writes:
            await asyncio.gather(*self._background_writes, return_exceptions=True)

    async def wait_for_submissions(self) -> None:
        """Drain event handlers already inside the serialized submission path."""
        async with self._lock:
            pass

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
        # Stage 1 keeps the historical key; add-on stages get their own suffix so every
        # stage has exactly one order row per market (a failed row is reset and retried).
        suffix = f":s{signal.stage}" if signal.stage > 1 else ""
        if policy == "one_per_side":
            return f"{mode}:{signal.market_id}:{signal.side}{suffix}"
        if policy == "allow":
            return f"{mode}:{signal.market_id}:{signal.side}:{signal.observed_at_ms // 1000}{suffix}"
        return f"{mode}:{signal.market_id}{suffix}"

    @staticmethod
    def _key_stage(order_key: str | None) -> int:
        match = _STAGE_SUFFIX.search(order_key or "")
        return int(match.group(1)) if match else 1

    def _log_trigger_once(self, signal: Signal, decision: str, reason: str, mode: str) -> None:
        if self._trigger_market != signal.market_id:
            self._trigger_market = signal.market_id
            self._trigger_seen.clear()
        key = (signal.market_id, signal.side, decision, signal.stage)
        if key in self._trigger_seen:
            return
        self._trigger_seen.add(key)
        self._schedule_trigger(signal, decision, reason, mode)

    @staticmethod
    def _persist_trigger(signal: Signal, decision: str, reason: str) -> None:
        try:
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
        except Exception:
            logger.exception("trigger event persistence failed")

    # ------------------------------------------------------------------
    async def handle_event(
        self,
        market: MarketInfo,
        evaluation: StrategyEvaluation,
        books: dict[str, OrderBook],
        mode: str,
        ctx: RiskContext | None,
    ) -> list[OrderResult]:
        config = self._runtime.get()
        async with self._lock:
            if not self._submission_allowed(mode):
                return [
                    OrderResult(None, signal.side, "BLOCKED", "bot_not_accepting_orders")
                    for signal in evaluation.signals
                ]

            if mode == "paper":
                await self._retry_partial_orders(market, books, mode, config)

            if not evaluation.signals or ctx is None:
                return []

            by_stage: dict[int, list[Signal]] = {}
            for signal in evaluation.signals:
                by_stage.setdefault(signal.stage, []).append(signal)

            results: list[OrderResult] = []
            # Stages are independent entries: each resolves its own UP/DOWN conflict and
            # gets its own order, in stage order.
            for stage in sorted(by_stage):
                stage_signals = by_stage[stage]
                chosen = self._choose(stage_signals, config.conflict_policy)
                for signal in stage_signals:
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
                    if signal.stage == 1 and result.status not in {
                        "REJECTED", "CANCELED", "BLOCKED", "SKIPPED"
                    }:
                        # later stages in this same pass see that stage 1 just bought
                        ctx = replace(
                            ctx,
                            stage1_bought=True,
                            stage1_remaining=self._stage1_remaining.get((mode, market.market_id)),
                        )
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
        decision = self._risk.check(signal, ctx, config)
        if not decision.approved:
            self._log_trigger_once(signal, f"blocked_{decision.reason}", decision.detail, mode)
            return OrderResult(None, signal.side, "BLOCKED", decision.reason)

        # Retry guard: a failed/cancelled entry is retried, but not in a tight loop and
        # not forever (every attempt is a real exchange order in live mode).
        attempts = self._attempts.get(key, 0)
        if attempts >= self._max_attempts:
            self._log_trigger_once(
                signal, "blocked_max_attempts", f"{attempts} attempts used for this entry", mode
            )
            return OrderResult(None, signal.side, "BLOCKED", "max_attempts")
        now_ms = clock.now_ms()
        if now_ms - self._last_attempt_ms.get(key, 0) < self._retry_gap_ms:
            return OrderResult(None, signal.side, "SKIPPED", "retry_cooldown")

        order_id, blocker_id, blocker_status = await asyncio.to_thread(
            self._reserve_order, mode, market, signal, key, config
        )
        if blocker_id is not None:
            reason = f"duplicate blocked by order #{blocker_id} ({blocker_status})"
            self._log_trigger_once(signal, "blocked_duplicate", reason, mode)
            return OrderResult(blocker_id, signal.side, "BLOCKED", "duplicate")
        if order_id is None:
            self._log_trigger_once(signal, "blocked_duplicate", "idempotency key already active", mode)
            return OrderResult(None, signal.side, "BLOCKED", "duplicate")

        if not self._submission_allowed(mode):
            await asyncio.to_thread(self._mark_not_submitted, order_id)
            return OrderResult(order_id, signal.side, "BLOCKED", "bot_not_accepting_orders")

        if len(self._attempts) > 500:  # keys are per market; drop old markets
            self._attempts.clear()
            self._last_attempt_ms.clear()
        self._attempts[key] = attempts + 1
        self._last_attempt_ms[key] = now_ms

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
            asset_version=market.asset_version,
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

        book_before = _book_top_asks(books.get(signal.token_id))
        report: ExecutionReport
        try:
            executor = self._executor_provider(mode)
            report = await executor.place_buy(request)
        except Exception as exc:
            logger.error("executor raised (%s)", type(exc).__name__)
            report = ExecutionReport(
                status="SUBMITTED" if mode == "live" else "REJECTED",
                error=(
                    "order submission outcome is unknown; reconcile before retrying"
                    if mode == "live"
                    else f"{type(exc).__name__}: {exc}"
                ),
            )

        result = await asyncio.to_thread(self._finalize_order, order_id, report, signal, mode)
        if result.status not in {"REJECTED", "CANCELED"}:
            # accepted/filled: the cooldown and attempt cap only apply to failed entries
            self._attempts.pop(key, None)
            self._last_attempt_ms.pop(key, None)
            if signal.stage == 1:
                if len(self._stage1_remaining) > 500:
                    self._stage1_remaining.clear()
                self._stage1_remaining[(mode, market.market_id)] = signal.remaining_seconds
        self._record_order_result(
            result,
            report,
            signal,
            mode,
            diagnostics={
                "book_at_submit": book_before,
                "book_after": _book_top_asks(books.get(signal.token_id)),
                "max_price": request.max_price,
                "stage": signal.stage,
                "remaining_seconds": signal.remaining_seconds,
                "attempt": attempts + 1,
            },
        )
        return result

    def _find_duplicate(self, session, mode: str, signal: Signal, policy: str) -> Order | None:
        query = select(Order).where(Order.mode == mode, Order.market_id == signal.market_id)
        if policy in {"one_per_market", "one_per_side"}:
            if policy == "one_per_side":
                query = query.where(Order.side == signal.side)
            orders = session.execute(query).scalars().all()
            for order in orders:
                # each entry stage has its own one-order allowance
                if self._key_stage(order.order_key) != signal.stage:
                    continue
                if order.status in ACTIVE_STATUSES or order.status == "FILLED" or order.filled_size > 0:
                    return order
            return None
        orders = session.execute(query.where(Order.status.in_(ACTIVE_STATUSES))).scalars().all()
        return orders[0] if orders else None

    def _reserve_order(
        self, mode: str, market: MarketInfo, signal: Signal, key: str, config
    ) -> tuple[int | None, int | None, str | None]:
        try:
            with session_scope() as session:
                blocker = self._find_duplicate(session, mode, signal, config.duplicate_policy)
                if blocker is not None:
                    return None, blocker.id, blocker.status
                existing = session.execute(
                    select(Order).where(Order.order_key == key)
                ).scalar_one_or_none()
                if existing is not None:
                    if existing.status in ACTIVE_STATUSES or existing.filled_size > 0:
                        return None, None, None
                    existing.status = "PENDING"
                    existing.requested_price = signal.best_ask
                    existing.submitted_price = None
                    existing.error = None
                    existing.reason = ""
                    existing.latency_ms = None
                    session.flush()
                    return existing.id, None, None
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
                return order.id, None, None
        except IntegrityError:
            return None, None, None

    @staticmethod
    def _mark_not_submitted(order_id: int) -> None:
        with session_scope() as session:
            order = session.get(Order, order_id)
            if order is not None and order.status == "PENDING":
                order.status = "CANCELED"
                order.error = "bot halted before exchange submission"

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

        return result

    def _record_order_result(
        self,
        result: OrderResult,
        report: ExecutionReport,
        signal: Signal,
        mode: str,
        diagnostics: dict | None = None,
    ) -> None:
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
                payload={
                    "order_id": result.order_id,
                    "side": signal.side,
                    "signal_ask": signal.best_ask,
                    "latency_ms": report.latency_ms,
                    "exchange_response": report.raw,
                    **(diagnostics or {}),
                },
                mode=mode,
            )

    # ------------------------------------------------------------------
    async def _retry_partial_orders(
        self, market: MarketInfo, books: dict[str, OrderBook], mode: str, config
    ) -> None:
        if market.remaining_seconds() <= 0:
            return
        pending = await asyncio.to_thread(self._partial_order_candidates, market.market_id, mode)

        for order_id, side, filled_size, avg_fill_price, requested_notional in pending:
            now_ms = clock.now_ms()
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
                asset_version=market.asset_version,
            )
            try:
                report = await self._executor_provider(mode).place_buy(request)
            except Exception as exc:
                logger.warning("partial retry failed (%s)", type(exc).__name__)
                continue
            if not report.fills:
                continue
            await asyncio.to_thread(self._apply_partial_retry, order_id, report, mode)
            self._logs.log(
                "INFO",
                "fill",
                f"partial retry filled {report.filled_size:.4f} @ {report.avg_price or 0:.4f}",
                payload={"order_id": order_id, "side": side, "status": report.status},
                mode=mode,
            )

    @staticmethod
    def _partial_order_candidates(market_id: str, mode: str) -> list[tuple[int, str, float, float, float]]:
        with session_scope() as session:
            orders = session.execute(
                select(Order).where(
                    Order.mode == mode,
                    Order.market_id == market_id,
                    Order.status == "PARTIALLY_FILLED",
                )
            ).scalars().all()
            return [
                (o.id, o.side, o.filled_size, o.avg_fill_price or 0.0, o.requested_notional)
                for o in orders
            ]

    def _apply_partial_retry(self, order_id: int, report: ExecutionReport, mode: str) -> None:
        with session_scope() as session:
            order = session.get(Order, order_id)
            if order is None or order.status != "PARTIALLY_FILLED":
                return
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

    # ------------------------------------------------------------------
    async def finalize_market(self, market_id: str, mode: str, reason: str = "market expired") -> int:
        if mode == "live":
            return await self.cancel_live_orders(market_id)
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

    async def cancel_live_orders(self, market_id: str | None = None) -> int:
        with session_scope() as session:
            query = select(Order).where(
                Order.mode == "live",
                Order.status.in_(ACTIVE_STATUSES),
                Order.exchange_order_id.is_not(None),
            )
            if market_id is not None:
                query = query.where(Order.market_id == market_id)
            orders = [(o.id, o.exchange_order_id) for o in session.execute(query).scalars().all()]

        engine = self._executor_provider("live")
        cancel_order = getattr(engine, "cancel_order", None)
        if not callable(cancel_order):
            return 0
        resolved = 0
        for order_id, exchange_id in orders:
            try:
                await cancel_order(exchange_id)
                await self.reconcile_live_order(order_id)
            except Exception as exc:
                logger.warning("live order cancellation/reconciliation failed (%s)", type(exc).__name__)
            with session_scope() as session:
                current = session.get(Order, order_id)
                if current is not None and current.status not in ACTIVE_STATUSES:
                    resolved += 1
        return resolved

    async def reconcile_live_order(self, order_id: int) -> None:
        with session_scope() as session:
            order = session.get(Order, order_id)
            if order is None or order.mode != "live" or not order.exchange_order_id:
                return
            exchange_id = order.exchange_order_id
            current_size = order.filled_size
            token_id = order.token_id
            current_price = order.avg_fill_price

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
        terminal = raw_status in {
            "CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED", "EXPIRED", "INVALID", "MATCHED", "FILLED"
        }
        if delta <= 0 and not terminal:
            return

        delta_price = None
        if delta > 0:
            fetch_fill_price = getattr(engine, "fetch_fill_price", None)
            if not callable(fetch_fill_price):
                return
            cumulative_price = await fetch_fill_price(
                exchange_id, list(payload.get("associate_trades") or []), size_matched
            )
            if cumulative_price is None:
                return
            prior_cost = (current_price or 0.0) * current_size
            delta_price = (cumulative_price * size_matched - prior_cost) / delta
            if delta_price <= 0:
                return

        fee = 0.0
        estimate_fee = getattr(engine, "estimate_fee", None)
        if delta > 0 and callable(estimate_fee):
            fee = float(estimate_fee(token_id, delta, delta_price))
        total_size = round(current_size + max(0.0, delta), 10)
        if raw_status in {"MATCHED", "FILLED"}:
            next_status = "FILLED"
        elif raw_status in {
            "CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED", "EXPIRED", "INVALID"
        }:
            next_status = "CANCELED"
        else:
            next_status = "PARTIALLY_FILLED" if total_size > 0 else "SUBMITTED"
        report = ExecutionReport(
            status=next_status,
            filled_size=max(0.0, delta),
            avg_price=delta_price,
            fee=fee,
            exchange_order_id=exchange_id,
            fills=[
                FillPart(
                    price=delta_price,
                    size=delta,
                    fee=fee,
                    exchange_trade_id=f"live-reconcile-{exchange_id}-{size_matched}",
                )
            ] if delta > 0 and delta_price is not None
            else [],
        )
        with session_scope() as session:
            order = session.get(Order, order_id)
            if order is None:
                return
            order.filled_size = round(current_size + max(0.0, delta), 10)
            order.fee = round(order.fee + fee, 10)
            if delta > 0 and delta_price is not None:
                order.avg_fill_price = round(
                    (((current_price or 0.0) * current_size) + delta_price * delta) / order.filled_size,
                    8,
                )
            order.status = next_status
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
