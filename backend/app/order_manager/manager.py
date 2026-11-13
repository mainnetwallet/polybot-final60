from __future__ import annotations

import asyncio
import logging
import math
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
from app.execution.base import ExecutionEngine, ExecutionReport, FillPart, OrderRequest, SellRequest
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
_SELL_STAGE = re.compile(r":s(\d+):sell:")
# Stage 0 take-profit SELL orders carry this marker in their order key.
_SELL_MARK = ":sell:"
MAX_SELL_ATTEMPTS = 30
SELL_DUST = 0.01
# Entry stages whose shares are watched for an exit: stages 0-2 have a take-profit sell, and every
# stage (0-3) has a stop-loss sell (stage 3 has no take-profit, it is held to settlement otherwise).
EXIT_STAGES = (0, 1, 2, 3)
# Worst acceptable price of a stop-loss sell: it is a market sell, the exit must not be refused
# because the bid keeps falling (the take-profit sell, by contrast, never sells below its price).
STOP_LOSS_FLOOR = 0.01


def _floor_shares(size: float) -> float:
    """Whole cents of a share, rounded down: a SELL must never ask for more than is held."""
    return math.floor(round(size * 100, 6) + 1e-9) / 100


@dataclass
class Stage0Holding:
    """Shares bought by stage 0 (or stage 1/2/3, see `stage`) in one market that have not been sold yet."""

    side: str
    token_id: str
    size: float
    sell_in_flight: bool = False
    cost: float | None = None  # average price paid per share (decides the take-profit tier and the stop-loss)
    stage: int = 0  # which entry stage bought these shares (0, 1, 2 or 3)


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
        # (mode, market) -> stage 0 shares still held; absent = not loaded from the database yet
        self._stage0_holdings: dict[tuple[str, str], Stage0Holding | None] = {}
        # same for the shares stage 1 bought (stage 1 take-profit sells)
        self._stage1_holdings: dict[tuple[str, str], Stage0Holding | None] = {}
        self._stage2_holdings: dict[tuple[str, str], Stage0Holding | None] = {}
        self._stage3_holdings: dict[tuple[str, str], Stage0Holding | None] = {}
        # (mode, market, stage, kind) with kind = take_profit | stop_loss: a take-profit that ran out of
        # attempts must not also lock the stop-loss of the same shares
        self._sell_attempts: dict[tuple[str, str, int, str], int] = {}
        self._last_sell_ms: dict[tuple[str, str, int, str], int] = {}
        # (mode, market) pairs where stage 0 already got an accepted buy (it buys once per market)
        self._stage0_bought: set[tuple[str, str]] = set()

    def _holdings(self, stage: int) -> dict[tuple[str, str], Stage0Holding | None]:
        if stage == 1:
            return self._stage1_holdings
        if stage == 2:
            return self._stage2_holdings
        if stage == 3:
            return self._stage3_holdings
        return self._stage0_holdings

    def _drop_holdings(self, mode: str, market_id: str) -> None:
        """Forget what is cached as held so the next exit pass reloads it from the database."""
        for stage in EXIT_STAGES:
            self._holdings(stage).pop((mode, market_id), None)

    def stage1_remaining(self, mode: str, market_id: str) -> float | None:
        return self._stage1_remaining.get((mode, market_id))

    def _schedule_trigger(self, signal: Signal, decision: str, reason: str, mode: str) -> None:
        async def persist_then_publish() -> None:
            await asyncio.to_thread(self._persist_trigger, signal, decision, reason)
            self._logs.log(
                "INFO" if decision == "submitted" else "WARNING",
                "trigger",
                f"{'S' + str(signal.stage) + ' ' if signal.stage != 1 else ''}"
                f"{signal.side} ask {signal.best_ask:.3f} {'<=' if signal.stage == 0 else '>='} "
                f"{signal.threshold:.2f} -> {decision}",
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
        suffix = f":s{signal.stage}" if signal.stage != 1 else ""
        if policy == "one_per_side":
            return f"{mode}:{signal.market_id}:{signal.side}{suffix}"
        if policy == "allow":
            return f"{mode}:{signal.market_id}:{signal.side}:{signal.observed_at_ms // 1000}{suffix}"
        return f"{mode}:{signal.market_id}{suffix}"

    @staticmethod
    def _key_stage(order_key: str | None) -> int:
        if _SELL_MARK in (order_key or ""):
            match = _SELL_STAGE.search(order_key or "")
            return int(match.group(1)) if match else 0  # sell keys end in :sN:sell:<id> (stage 0-3); a sell key without the marker counts as stage 0
        match = _STAGE_SUFFIX.search(order_key or "")
        return int(match.group(1)) if match else 1

    @staticmethod
    def _key_action(order_key: str | None) -> str:
        return "SELL" if _SELL_MARK in (order_key or "") else "BUY"

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
            # stage 0 never pays more than its own ask limit
            max_price=config.stage0_max_ask if signal.stage == 0 else config.max_execution_price,
            observed_at_ms=signal.observed_at_ms,
            order_min_size=market.min_order_size,
            asset_version=market.asset_version,
        )

        self._logs.log(
            "INFO",
            "order_submit",
            f"{'S0 ' if signal.stage == 0 else ''}marketable BUY {signal.side} "
            f"{config.trade_amount_usdc:.2f} USDC at ask {signal.best_ask:.3f}",
            payload={
                "market_id": market.market_id,
                "side": signal.side,
                "best_ask": signal.best_ask,
                "notional": config.trade_amount_usdc,
                "max_price": request.max_price,
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
        if signal.stage in (1, 2, 3):
            self._holdings(signal.stage).pop((mode, market.market_id), None)  # reload what is held
        if signal.stage == 0:
            self._stage0_holdings.pop((mode, market.market_id), None)  # reload what is held
            if result.status not in {"REJECTED", "CANCELED"}:
                if len(self._stage0_bought) > 500:
                    self._stage0_bought.clear()
                self._stage0_bought.add((mode, market.market_id))
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
                # each entry stage has its own one-order allowance (take-profit sells are not entries)
                if _SELL_MARK in (order.order_key or "") or self._key_stage(order.order_key) != signal.stage:
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

        for order_id, side, filled_size, avg_fill_price, requested_notional, order_key in pending:
            now_ms = clock.now_ms()
            last = self._retry_after.get(order_id, 0.0)
            if now_ms - last < 500:
                continue
            stage = self._key_stage(order_key)
            max_price = config.max_execution_price
            if stage == 0:
                remaining_s = market.remaining_seconds()
                if not (config.stage0_enabled and config.final_window_seconds < remaining_s <= config.stage0_start_seconds):
                    continue  # stage 0 stops buying when its window is over
                max_price = config.stage0_max_ask
            book = books.get(market.token_for(side))
            if book is None or book.best_ask is None or book.ask_size is None or book.ask_size <= 0:
                continue
            if book.best_ask > max_price + 1e-9:
                continue
            if stage == 0 and book.best_ask + 1e-9 < config.stage0_min_ask:
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
                max_price=max_price,
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
            if stage in EXIT_STAGES:
                self._holdings(stage).pop((mode, market.market_id), None)
            self._logs.log(
                "INFO",
                "fill",
                f"partial retry filled {report.filled_size:.4f} @ {report.avg_price or 0:.4f}",
                payload={"order_id": order_id, "side": side, "status": report.status},
                mode=mode,
            )

    @staticmethod
    def _partial_order_candidates(market_id: str, mode: str) -> list[tuple[int, str, float, float, float, str]]:
        with session_scope() as session:
            orders = session.execute(
                select(Order).where(
                    Order.mode == mode,
                    Order.market_id == market_id,
                    Order.status == "PARTIALLY_FILLED",
                )
            ).scalars().all()
            return [
                (o.id, o.side, o.filled_size, o.avg_fill_price or 0.0, o.requested_notional, o.order_key)
                for o in orders
                if _SELL_MARK not in o.order_key  # a partly filled SELL must never be re-bought
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
            is_sell = self._key_action(order.order_key) == "SELL"
            order_stage = self._key_stage(order.order_key)
            order_market_id = order.market_id

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
            if is_sell:
                self._portfolio.apply_sell_report(
                    session, "live", order, report,
                    basis=self.stage0_basis(session, "live", order.market_id, order.side, order_stage),
                )
            else:
                self._portfolio.apply_report(session, "live", order, report)
        self._drop_holdings("live", order_market_id)  # reload what is still held
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
        self._stage0_holdings.clear()  # re-read from the database on the next event
        self._stage1_holdings.clear()
        self._stage2_holdings.clear()
        self._stage3_holdings.clear()
        if len(self._sell_attempts) > 500:
            self._sell_attempts.clear()
            self._last_sell_ms.clear()

    # ------------------------------------------------------------------
    # Stage 0 take-profit: sell what stage 0 bought once the best bid reaches the sell price
    # ------------------------------------------------------------------
    @staticmethod
    def stage0_holding_in_session(
        session, mode: str, market_id: str, stage: int = 0
    ) -> Stage0Holding | None:
        orders = session.execute(
            select(Order).where(Order.mode == mode, Order.market_id == market_id)
        ).scalars().all()
        buys = [
            o for o in orders
            if o.order_key and _SELL_MARK not in o.order_key
            and OrderManager._key_stage(o.order_key) == stage and o.filled_size > 0
        ]
        if not buys:
            return None
        side, token_id = buys[0].side, buys[0].token_id
        sells = [
            o for o in orders
            if _SELL_MARK in (o.order_key or "") and o.side == side
            and OrderManager._key_stage(o.order_key) == stage
        ]
        held = sum(o.filled_size for o in buys if o.side == side) - sum(o.filled_size for o in sells)
        position = session.execute(
            select(Position).where(
                Position.mode == mode, Position.market_id == market_id, Position.side == side
            )
        ).scalar_one_or_none()
        if position is None or position.status != "open":
            return None
        held = min(held, position.size)
        in_flight = any(o.status in ACTIVE_STATUSES for o in sells)
        if held < SELL_DUST and not in_flight:
            return None
        side_buys = [o for o in buys if o.side == side]
        bought = sum(o.filled_size for o in side_buys)
        cost = (
            sum(o.filled_size * (o.avg_fill_price or 0.0) for o in side_buys) / bought
            if bought > 0 and all(o.avg_fill_price for o in side_buys)
            else None
        )
        return Stage0Holding(
            side=side, token_id=token_id, size=round(held, 10), sell_in_flight=in_flight,
            cost=cost, stage=stage,
        )

    @staticmethod
    def stage0_basis(
        session, mode: str, market_id: str, side: str, stage: int = 0
    ) -> tuple[float, float] | None:
        """(cost per share, buy fee per share) of the shares `stage` bought in this market/side."""
        buys = [
            o for o in session.execute(
                select(Order).where(Order.mode == mode, Order.market_id == market_id, Order.side == side)
            ).scalars().all()
            if o.order_key and _SELL_MARK not in o.order_key
            and OrderManager._key_stage(o.order_key) == stage and o.filled_size > 0
        ]
        shares = sum(o.filled_size for o in buys)
        if shares <= 0:
            return None
        cost = sum(o.filled_size * (o.avg_fill_price or 0.0) for o in buys)
        fees = sum(o.fee or 0.0 for o in buys)
        return cost / shares, fees / shares

    def load_stage0_holding(self, mode: str, market_id: str, stage: int = 0) -> Stage0Holding | None:
        with session_scope() as session:
            return self.stage0_holding_in_session(session, mode, market_id, stage)

    def stage0_bought(self, mode: str, market_id: str) -> bool:
        return (mode, market_id) in self._stage0_bought

    def exit_check_needed(self, mode: str, market_id: str) -> bool:
        """False once we know nothing is held for stage 0 in this market (keeps the hot path cheap)."""
        key = (mode, market_id)
        return any(
            key not in holdings or holdings[key] is not None
            for holdings in (
                self._stage0_holdings,
                self._stage1_holdings,
                self._stage2_holdings,
                self._stage3_holdings,
            )
        )

    def has_stage0_holding(self, mode: str, market_id: str) -> bool:
        """True while a take-profit or stop-loss sell may still be due (stage 0, 1, 2 or 3 shares held)."""
        key = (mode, market_id)
        for later in (1, 2, 3):
            held = self._holdings(later).get(key)
            if held is not None and not held.sell_in_flight:
                return True
        if key not in self._stage0_holdings:
            # not loaded yet (e.g. right after the buy): keep the retry loop awake for this market
            return key in self._stage0_bought
        holding = self._stage0_holdings[key]
        return holding is not None and not holding.sell_in_flight

    async def handle_exits(
        self, market: MarketInfo, books: dict[str, OrderBook], mode: str, config
    ) -> list[OrderResult]:
        """Exit sells: take-profit for stage 0 (15 / 10 / 5 % above the buy price), stage 1 (0.95 / 0.99 by cost) and stage 2 (0.99)
        shares, and a stop-loss (percent below the buy price) for the shares of every stage 0-3."""
        if market.remaining_seconds() <= 0:
            return []
        results: list[OrderResult] = []
        for stage in EXIT_STAGES:
            results.extend(await self._handle_stage_exit(market, books, mode, config, stage))
        return results

    async def _handle_stage_exit(
        self, market: MarketInfo, books: dict[str, OrderBook], mode: str, config, stage: int
    ) -> list[OrderResult]:
        """Sell the shares `stage` bought in this market: at its take-profit price once the best bid
        reaches it, or at the market once the best bid falls to its stop-loss level."""
        reg_key = (mode, market.market_id)
        holdings = self._holdings(stage)
        async with self._lock:
            if not self._submission_allowed(mode):
                return []
            if reg_key not in holdings:
                holdings[reg_key] = await asyncio.to_thread(
                    self.load_stage0_holding, mode, market.market_id, stage
                )
            holding = holdings[reg_key]
            if holding is None or holding.sell_in_flight:
                return []
            sell_size = _floor_shares(holding.size)
            if sell_size < SELL_DUST:
                holdings[reg_key] = None
                return []
            book = books.get(holding.token_id)
            bid = book.best_bid if book is not None else None
            if stage == 0:
                sell_price = config.stage0_sell_price_for(holding.cost)
            elif stage == 1:
                sell_price = config.stage1_sell_price_for(holding.cost)
            elif stage == 2:
                sell_price = config.stage2_sell_price_for(holding.cost)
            else:
                sell_price = None  # stage 3 has no take-profit: held to settlement unless its stop-loss fires
            stop_price = config.stop_loss_price_for(stage, holding.cost)
            if bid is None:
                return []
            if stop_price is not None and bid <= stop_price + 1e-9:
                kind, floor = "stop_loss", STOP_LOSS_FLOOR
            elif sell_price is not None and bid + 1e-9 >= sell_price:
                kind, floor = "take_profit", sell_price
            else:
                return []  # neither exit is due (or both are switched off): held to settlement
            sell_key = (mode, market.market_id, stage, kind)
            attempts = self._sell_attempts.get(sell_key, 0)
            if attempts >= MAX_SELL_ATTEMPTS:
                return [OrderResult(None, holding.side, "BLOCKED", "max_sell_attempts")]
            now_ms = clock.now_ms()
            if now_ms - self._last_sell_ms.get(sell_key, 0) < self._retry_gap_ms:
                return []
            self._sell_attempts[sell_key] = attempts + 1
            self._last_sell_ms[sell_key] = now_ms

            key = f"{mode}:{market.market_id}:{holding.side}:s{stage}{_SELL_MARK}{uuid.uuid4().hex[:8]}"
            order_id = await asyncio.to_thread(
                self._reserve_sell_order, mode, market, holding, key, sell_size, bid, kind
            )
            stop_note = (
                f" (stop-loss: bid <= {stop_price:.3f}, {config.stop_loss_pct_for(stage):g}% under cost)"
                if kind == "stop_loss"
                else ""
            )
            self._logs.log(
                "WARNING" if kind == "stop_loss" else "INFO",
                "order_submit",
                f"S{stage} {'STOP-LOSS ' if kind == 'stop_loss' else ''}marketable SELL "
                f"{holding.side} {sell_size:.2f} shares at bid {bid:.3f}{stop_note}",
                payload={
                    "market_id": market.market_id,
                    "side": holding.side,
                    "size": sell_size,
                    "best_bid": bid,
                    "min_price": floor,
                    "exit_kind": kind,
                    "stop_price": stop_price,
                    "buy_cost": holding.cost,
                    "mode": mode,
                    "order_key": key,
                    "attempt": attempts + 1,
                },
                mode=mode,
            )
            request = SellRequest(
                order_key=key,
                client_order_id=f"ord-{uuid.uuid4().hex[:20]}",
                market_id=market.market_id,
                side=holding.side,
                token_id=holding.token_id,
                size=sell_size,
                min_price=floor,
                observed_at_ms=now_ms,
                order_min_size=market.min_order_size,
                asset_version=market.asset_version,
            )
            book_before = {
                "bids": [[p, sz] for p, sz in book.bid_levels()[:3]] if book is not None else None,
                "age_ms": round(book.age_ms()) if book is not None and book.updated_at_ms else None,
            }
            try:
                report = await self._executor_provider(mode).place_sell(request)
            except Exception as exc:
                logger.error("executor raised on sell (%s)", type(exc).__name__)
                report = ExecutionReport(
                    status="SUBMITTED" if mode == "live" else "REJECTED",
                    error=(
                        "sell submission outcome is unknown; reconcile before retrying"
                        if mode == "live"
                        else f"{type(exc).__name__}: {exc}"
                    ),
                )
            result = await asyncio.to_thread(
                self._finalize_sell_order,
                order_id,
                report,
                holding.side,
                mode,
                stage,
                f"stage{stage}_stop_loss" if kind == "stop_loss" else None,
            )
            holdings[reg_key] = await asyncio.to_thread(
                self.load_stage0_holding, mode, market.market_id, stage
            )
            if report.fills:
                self._sell_attempts.pop(sell_key, None)
                self._logs.log(
                    "INFO",
                    "fill",
                    f"S{stage} sold {report.filled_size:.4f} shares @ {report.avg_price or 0:.4f}",
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
                    "WARNING" if report.fills or report.status == "CANCELED" else "ERROR",
                    "error",
                    f"S{stage} sell {report.status}: {report.error or 'rejected'}",
                    payload={
                        "order_id": result.order_id,
                        "side": holding.side,
                        "best_bid": bid,
                        "exchange_response": report.raw,
                        "book_at_submit": book_before,
                        "attempt": attempts + 1,
                    },
                    mode=mode,
                )
            return [result]

    @staticmethod
    def _reserve_sell_order(
        mode: str,
        market: MarketInfo,
        holding: Stage0Holding,
        key: str,
        size: float,
        bid: float,
        kind: str = "take_profit",
    ) -> int:
        with session_scope() as session:
            order = Order(
                order_key=key,
                client_order_id=f"ord-{uuid.uuid4().hex[:20]}",
                mode=mode,
                market_id=market.market_id,
                side=holding.side,
                token_id=holding.token_id,
                status="PENDING",
                requested_notional=round(size * bid, 6),
                requested_price=bid,
                reason=f"stage{holding.stage}_{kind}",
            )
            session.add(order)
            session.flush()
            return order.id

    def _finalize_sell_order(
        self,
        order_id: int,
        report: ExecutionReport,
        side: str,
        mode: str,
        stage: int = 0,
        reason: str | None = None,
    ) -> OrderResult:
        with session_scope() as session:
            order = session.get(Order, order_id)
            if order is None:
                return OrderResult(None, side, "REJECTED", "missing_order")
            order.status = report.status
            order.filled_size = round(order.filled_size + report.filled_size, 10)
            if report.filled_size > 0 and report.avg_price:
                order.avg_fill_price = report.avg_price
            order.fee = round(order.fee + report.fee, 10)
            order.submitted_price = report.submitted_price or order.submitted_price
            order.exchange_order_id = report.exchange_order_id or order.exchange_order_id
            order.latency_ms = report.latency_ms
            order.error = report.error
            self._portfolio.apply_sell_report(
                session, mode, order, report,
                basis=self.stage0_basis(session, mode, order.market_id, order.side, stage),
            )
            result = OrderResult(
                order.id,
                side,
                order.status,
                reason or f"stage{stage}_sell",
                order.filled_size,
                order.avg_fill_price,
                order.latency_ms,
                order.exchange_order_id,
                order.error,
            )
            session.flush()
        return result


__all__ = ["OrderManager", "OrderResult", "datetime", "timezone", "Position"]
