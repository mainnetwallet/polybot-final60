"""Trading bot orchestrator: lifecycle, safety gates, snapshots and settlement."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import deque
from datetime import datetime, timezone

from sqlalchemy import select

from app.config.runtime import SettingsService
from app.config.settings import Settings
from app.database.models import AppState, Market, Order, Position, TriggerEvent, utcnow
from app.database.session import session_scope
from app.execution.base import ExecutionEngine
from app.live_trading.engine import LiveExecutionEngine, LiveTradingUnavailable
from app.market_data.client import ClobMarketDataClient
from app.market_discovery.service import MarketDiscovery, MarketMonitor
from app.market_discovery.types import MarketInfo
from app.monitoring.logs import LogService, Publisher
from app.order_manager.manager import OrderManager, OrderResult
from app.paper_trading.engine import PaperExecutionEngine
from app.portfolio.analytics import AnalyticsService
from app.portfolio.service import Portfolio
from app.risk_manager.manager import RiskContext, RiskManager
from app.strategy.engine import FinalWindowStrategy
from app.strategy.types import StrategyEvaluation

logger = logging.getLogger(__name__)


class BotError(RuntimeError):
    pass


class ModeSwitchError(BotError):
    pass


class LiveGateError(BotError):
    pass


def iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TradingBot:
    def __init__(
        self,
        settings: Settings,
        runtime: SettingsService,
        portfolio: Portfolio,
        analytics: AnalyticsService,
        hub: Publisher,
    ) -> None:
        self.settings = settings
        self.runtime = runtime
        self.portfolio = portfolio
        self.analytics = analytics
        self.hub = hub

        self.logs = LogService(publisher=hub)
        self.strategy = FinalWindowStrategy()
        self.risk = RiskManager()

        self.discovery = MarketDiscovery(settings)
        self.monitor = MarketMonitor(self.discovery, settings, self._on_market)
        self.data = ClobMarketDataClient(settings, self._on_book)

        self.paper_engine = PaperExecutionEngine(settings, self.data.book_for)
        self.live_engine = LiveExecutionEngine(settings, balance_hint=lambda: self._live_balance)
        self.orders = OrderManager(
            settings, runtime, portfolio, self.risk, self._executor_for, self.logs
        )

        self._status = "STOPPED"
        self._mode = "paper"
        self._kill_switch = False
        self._started_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._market: MarketInfo | None = None
        self._evaluation: StrategyEvaluation | None = None
        self._last_order: dict | None = None
        self._latencies: deque[float] = deque(maxlen=50)
        self._live_balance: float | None = None
        self._live_balance_at = 0.0
        self._last_price_persist_ms: dict[str, int] = {}
        self._tasks: list[asyncio.Task] = []
        self._shutdown = False
        self._market_finalized: set[str] = set()
        self._resolution_pending_logged: set[str] = set()
        self._last_resolution_poll = 0.0
        self._last_live_reconcile = 0.0
        self._risk_halt_reason: str | None = None
        self._risk_halt_logged = False
        self._tick_pending = False

    @property
    def status(self) -> str:
        return self._status

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def current_market(self) -> MarketInfo | None:
        return self._market

    # ------------------------------------------------------------------
    def _executor_for(self, mode: str) -> ExecutionEngine:
        if mode == "live":
            return self.live_engine
        return self.paper_engine

    async def initialize(self) -> None:
        self._shutdown = False
        with session_scope() as session:
            self.portfolio.ensure_accounts(session)
            self.runtime.load(session)
            state = session.get(AppState, 1)
            if state is None:
                state = AppState(id=1, status="STOPPED", mode="paper", kill_switch=False)
                session.add(state)
            self._mode = state.mode if state.mode in {"paper", "live"} else "paper"
            self._kill_switch = bool(state.kill_switch)
            self._status = "STOPPED"
            self._started_at = None
            self._market = None
            self._evaluation = None
            self._last_order = None
            state.status = "STOPPED"
            state.started_at = None
            session.flush()
            recovered = session.execute(
                select(Order).where(Order.status.in_(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]))
            ).scalars().all()
            for order in recovered:
                order.status = "EXPIRED"
                order.reason = "recovered after restart; open remainder cancelled"
            recovered_count = len(recovered)
            paper_open = session.execute(
                select(Position).where(Position.mode == "paper", Position.status == "open")
            ).scalars().all()
            live_open = session.execute(
                select(Position).where(Position.mode == "live", Position.status == "open")
            ).scalars().all()
            paper_open_count, live_open_count = len(paper_open), len(live_open)

        await self.discovery.start()
        await self.data.start()
        await self.monitor.start()
        self.logs.start()
        self._tasks = [
            asyncio.create_task(self._state_broadcast_loop(), name="state-broadcast"),
            asyncio.create_task(self._maintenance_loop(), name="maintenance"),
        ]
        self.logs.log(
            "INFO",
            "system",
            f"backend started; mode={self._mode} status=STOPPED",
            payload={
                "recovered_orders": recovered_count,
                "paper_open_positions": paper_open_count,
                "live_open_positions": live_open_count,
            },
            mode=self._mode,
        )
        if live_open_count and self._mode == "live":
            await self._reconcile_live_positions()

    async def shutdown(self) -> None:
        if self._status == "RUNNING":
            try:
                await self.stop_bot("shutdown")
            except Exception as exc:
                logger.warning("shutdown stop failed: %s", exc)
        self._shutdown = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.monitor.stop()
        await self.data.stop()
        await self.discovery.close()
        await self.live_engine.close()
        await self.logs.stop()

    # ------------------------------------------------------------------
    async def _on_market(self, market: MarketInfo | None, event: str) -> None:
        if market is not None:
            previous = self._market
            self._market = market
            if previous is not None and previous.market_id != market.market_id:
                await self.orders.finalize_market(previous.market_id, self._mode, "market changed")
            self._persist_market(market)
            self.monitor.track(market)
            self.data.set_market(market)
            if self._mode == "live":
                asyncio.create_task(
                    self.live_engine.prewarm([market.up_token_id, market.down_token_id])
                )
            self.orders.reset_trigger_cache()
            self._evaluation = None
            self._market_finalized.discard(market.market_id)
            self.logs.log(
                "INFO",
                "market",
                f"market opened {market.slug} ({iso_z(market.window_start)} -> {iso_z(market.window_end)})",
                payload={
                    "market_id": market.market_id,
                    "slug": market.slug,
                    "up_token_id": market.up_token_id,
                    "down_token_id": market.down_token_id,
                    "window_start": iso_z(market.window_start),
                    "window_end": iso_z(market.window_end),
                },
                mode=self._mode,
            )
            await self._broadcast_state()
        else:
            previous = self._market
            if previous is not None:
                self._market = None
                self.data.set_market(None)
                self._evaluation = None
                if previous.market_id not in self._market_finalized:
                    await self.orders.finalize_market(previous.market_id, self._mode, "market closed")
                    self._market_finalized.add(previous.market_id)
                self.logs.log(
                    "INFO",
                    "market",
                    f"market closed {previous.slug}; awaiting resolution",
                    payload={"market_id": previous.market_id},
                    mode=self._mode,
                )
                await self._broadcast_state()

    def _persist_market(self, market: MarketInfo) -> None:
        with session_scope() as session:
            row = session.execute(
                select(Market).where(Market.market_id == market.market_id)
            ).scalar_one_or_none()
            if row is None:
                row = Market(market_id=market.market_id)
                session.add(row)
            row.slug = market.slug
            row.event_slug = market.event_slug
            row.series_id = market.series_id
            row.window_start = market.window_start
            row.window_end = market.window_end
            row.up_token_id = market.up_token_id
            row.down_token_id = market.down_token_id
            row.tick_size = market.tick_size
            row.min_order_size = market.min_order_size
            row.status = "active"
            session.flush()

    async def _on_book(self, market_id: str, side: str, book) -> None:
        market = self._market
        if market is None or market.market_id != market_id:
            return
        config = self.runtime.get()
        now_ms = int(time.time() * 1000)
        last_persist = self._last_price_persist_ms.get(side, 0)
        if now_ms - last_persist >= self.settings.ws_price_persist_interval_ms:
            self._last_price_persist_ms[side] = now_ms
            self._persist_price(market_id, side, book)

        evaluation = self.strategy.evaluate(market, self.data.books, now_ms, config)
        self._evaluation = evaluation
        self._schedule_tick()
        if self._status != "RUNNING":
            return

        # The risk context hits the database; only build it when a signal needs it.
        ctx = None
        if evaluation.signals:
            try:
                ctx = self._risk_context(market, config, now_ms)
            except Exception as exc:
                logger.exception("risk context failed")
                self.logs.log("ERROR", "error", f"risk context failed: {exc}", mode=self._mode)
                return

        try:
            results = await self.orders.handle_event(
                market, evaluation, self.data.books, self._mode, ctx
            )
        except Exception as exc:
            logger.exception("order handling failed")
            self.logs.log("ERROR", "error", f"order handling failed: {exc}", mode=self._mode)
            return
        for result in results:
            if result.status not in {"SKIPPED", "BLOCKED"}:
                self._last_order = result.as_dict()
                self._last_order["ts"] = iso_z(utcnow())
                if result.latency_ms is not None:
                    self._latencies.append(result.latency_ms)
        if results:
            await self._broadcast_state()

    # ------------------------------------------------------------------
    def _schedule_tick(self) -> None:
        """Push books/countdown to the dashboard on every book event (coalesced, no timer)."""
        if self._tick_pending or not getattr(self.hub, "client_count", 0):
            return
        self._tick_pending = True
        asyncio.get_running_loop().create_task(self._send_tick())

    async def _send_tick(self) -> None:
        self._tick_pending = False
        try:
            market = self._market
            evaluation = self._evaluation
            up_book = self.data.book_for("UP")
            down_book = self.data.book_for("DOWN")
            await self.hub.broadcast(
                {
                    "type": "tick",
                    "data": {
                        "server_time_ms": int(time.time() * 1000),
                        "remaining_seconds": round(market.remaining_seconds(), 3) if market else None,
                        "books": {
                            "up": up_book.snapshot() if up_book else None,
                            "down": down_book.snapshot() if down_book else None,
                        },
                        "strategy": evaluation.summary() if evaluation else None,
                    },
                }
            )
        except Exception as exc:
            logger.debug("tick broadcast failed: %s", exc)

    def _persist_price(self, market_id: str, side: str, book) -> None:
        from app.database.models import PriceUpdate

        try:
            with session_scope() as session:
                session.add(
                    PriceUpdate(
                        market_id=market_id,
                        side=side,
                        best_bid=book.best_bid,
                        best_ask=book.best_ask,
                        bid_size=book.bid_size,
                        ask_size=book.ask_size,
                        source=book.source,
                        ts=book.updated_at_ms,
                    )
                )
        except Exception as exc:  # price history must never break trading
            logger.warning("price persist failed: %s", exc)

    def _risk_context(self, market: MarketInfo, config, now_ms: int) -> RiskContext:
        marks = self._marks()
        with session_scope() as session:
            unrealized = self.portfolio.mark_to_market(session, self._mode, marks, market.market_id)
            pnl = self.portfolio.pnl_summary(session, self._mode, unrealized)["daily_pnl"]
            trades_today = self.portfolio.trades_today(session, self._mode)
            open_positions = len(self.portfolio.open_positions(session, self._mode))
            balance = self._balance(session)
        remaining = market.remaining_seconds()
        return RiskContext(
            now_ms=now_ms,
            kill_switch=self._kill_switch,
            account_balance=balance,
            daily_pnl=pnl,
            trades_today=trades_today,
            open_positions=open_positions,
            market_active=True,
            market_expired=remaining <= 0,
            remaining_seconds=remaining,
        )

    def _balance(self, session) -> float:
        if self._mode == "live" and self._live_balance is not None:
            return self._live_balance
        return self.portfolio.balance(session, self._mode)

    def _marks(self) -> dict[tuple[str, str], float]:
        market = self._market
        if market is None:
            return {}
        marks: dict[tuple[str, str], float] = {}
        for side in ("UP", "DOWN"):
            book = self.data.book_for(side)
            if book is not None and book.best_bid is not None:
                marks[(market.market_id, side)] = book.best_bid
        return marks

    # ------------------------------------------------------------------
    async def _state_broadcast_loop(self) -> None:
        while not self._shutdown:
            await asyncio.sleep(0.5)
            try:
                if getattr(self.hub, "client_count", 0):
                    await self._broadcast_state()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("state broadcast failed: %s", exc)

    async def _broadcast_state(self) -> None:
        try:
            snapshot = await self.snapshot()
            await self.hub.broadcast({"type": "state", "data": snapshot})
        except Exception as exc:
            logger.debug("snapshot failed: %s", exc)

    # ------------------------------------------------------------------
    async def start_bot(self, confirm_live: bool = False, confirmation_phrase: str = "") -> dict:
        async with self._lock:
            if self._status == "RUNNING":
                return await self.snapshot()
            if self._kill_switch:
                raise BotError("kill switch is active; clear it before starting")
            gate = await self._live_gate(self._mode, confirm_live, confirmation_phrase, validate=True)
            self._status = "RUNNING"
            self._started_at = utcnow()
            with session_scope() as session:
                state = session.get(AppState, 1)
                if state:
                    state.status = "RUNNING"
                    state.started_at = self._started_at
            self._risk_halt_reason = None
            self._risk_halt_logged = False
            self.orders.reset_trigger_cache()
            self.logs.log(
                "INFO",
                "system",
                f"bot started in {self._mode.upper()} mode",
                payload={"mode": self._mode, "live_balance": gate.get("balance")},
                mode=self._mode,
            )
        await self._broadcast_state()
        return await self.snapshot()

    async def stop_bot(self, reason: str = "operator") -> dict:
        async with self._lock:
            if self._status != "RUNNING":
                return await self.snapshot()
            self._status = "STOPPED"
            self._started_at = None
            with session_scope() as session:
                state = session.get(AppState, 1)
                if state:
                    state.status = "STOPPED"
                    state.started_at = None
            market = self._market
            if market is not None:
                await self.orders.finalize_market(market.market_id, self._mode, f"bot stopped ({reason})")
            if self._mode == "live":
                await self.live_engine.cancel_open_orders()
            self.logs.log(
                "WARNING",
                "system",
                f"bot stopped ({reason})",
                payload={"mode": self._mode},
                mode=self._mode,
            )
        await self._broadcast_state()
        return await self.snapshot()

    async def switch_mode(
        self, mode: str, confirm_live: bool = False, confirmation_phrase: str = ""
    ) -> dict:
        if mode not in {"paper", "live"}:
            raise ModeSwitchError("mode must be 'paper' or 'live'")
        async with self._lock:
            if self._status == "RUNNING":
                raise ModeSwitchError("stop the bot before switching modes")
            with session_scope() as session:
                active = session.execute(
                    select(Order).where(Order.status.in_(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]))
                ).scalars().all()
                if active:
                    raise ModeSwitchError(
                        f"{len(active)} outstanding order(s) must be resolved before switching"
                    )
            gate = await self._live_gate(mode, confirm_live, confirmation_phrase, validate=True)
            previous = self._mode
            self._mode = mode
            with session_scope() as session:
                state = session.get(AppState, 1)
                if state:
                    state.mode = mode
            self._evaluation = None
            self._last_order = None
            self._live_balance = gate.get("balance") if mode == "live" else None
            self.logs.log(
                "WARNING" if mode == "live" else "INFO",
                "system",
                f"execution mode switched {previous.upper()} -> {mode.upper()}",
                payload={"previous": previous, "mode": mode},
                mode=mode,
            )
            if mode == "live":
                await self._reconcile_live_positions()
        await self._broadcast_state()
        return await self.snapshot()

    async def _live_gate(
        self, mode: str, confirm_live: bool, phrase: str, validate: bool
    ) -> dict:
        if mode != "live":
            return {}
        if not confirm_live:
            raise LiveGateError("live trading requires explicit confirmation")
        expected = self.settings.live_confirmation_phrase
        if not phrase or not secrets.compare_digest(phrase.strip(), expected):
            raise LiveGateError("confirmation phrase does not match")
        if not self.settings.live_trading_enabled:
            raise LiveGateError("LIVE_TRADING_ENABLED is false in .env")
        if not self.settings.polymarket_geo_verified:
            raise LiveGateError(
                "POLYMARKET_GEO_VERIFIED is false; verify geo eligibility before live trading"
            )
        if not self.settings.live_credentials_present:
            raise LiveGateError("POLYMARKET_PRIVATE_KEY is not configured")
        if validate:
            try:
                result = await self.live_engine.validate()
            except LiveTradingUnavailable as exc:
                raise LiveGateError(str(exc)) from exc
            self._live_balance = float(result["balance"])
            self._live_balance_at = time.monotonic()
            with session_scope() as session:
                self.portfolio.set_cached_balance(session, "live", self._live_balance)
            return result
        return {}

    async def set_kill_switch(self, active: bool) -> dict:
        if active:
            self._kill_switch = True
            with session_scope() as session:
                state = session.get(AppState, 1)
                if state:
                    state.kill_switch = True
            await self.stop_bot("kill switch")
            await self.live_engine.cancel_open_orders()
        else:
            async with self._lock:
                if self._status == "RUNNING":
                    raise BotError("stop the bot before clearing the kill switch")
                self._kill_switch = False
                with session_scope() as session:
                    state = session.get(AppState, 1)
                    if state:
                        state.kill_switch = False
        self.logs.log(
            "CRITICAL" if active else "WARNING",
            "system",
            f"kill switch {'ENGAGED' if active else 'cleared'}",
            mode=self._mode,
        )
        await self._broadcast_state()
        return await self.snapshot()

    # ------------------------------------------------------------------
    async def _maintenance_loop(self) -> None:
        while not self._shutdown:
            await asyncio.sleep(2.0)
            try:
                await self._expire_market_if_due()
                now = time.monotonic()
                if now - self._last_resolution_poll >= self.settings.resolution_poll_seconds:
                    self._last_resolution_poll = now
                    await self._poll_resolutions()
                if (
                    self._mode == "live"
                    and self._status == "RUNNING"
                    and now - self._last_live_reconcile >= 3.0
                ):
                    self._last_live_reconcile = now
                    await self._reconcile_open_live_orders()
                if self._mode == "live" and now - self._live_balance_at >= 15.0:
                    await self._refresh_live_balance()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("maintenance loop error: %s", exc)

    async def _expire_market_if_due(self) -> None:
        market = self._market
        if market is None or market.remaining_seconds() > 0:
            return
        if market.market_id in self._market_finalized:
            return
        await self.orders.finalize_market(market.market_id, self._mode, "market expired")
        self._market_finalized.add(market.market_id)
        self.logs.log(
            "INFO",
            "market",
            f"final 60s window closed for {market.slug}",
            payload={"market_id": market.market_id},
            mode=self._mode,
        )
        self._market = None
        self.data.set_market(None)
        await self._broadcast_state()

    async def _poll_resolutions(self) -> None:
        results = await self.monitor.poll_resolution()
        for info in results:
            with session_scope() as session:
                row = session.execute(
                    select(Market).where(Market.market_id == info.market_id)
                ).scalar_one_or_none()
                if row is not None:
                    row.status = "resolved" if info.outcome else "closed"
                if info.outcome and row is not None:
                    row.resolved_outcome = info.outcome
                    row.resolved_at = utcnow()
            if info.outcome:
                for mode in ("paper", "live"):
                    with session_scope() as session:
                        closed = self.portfolio.settle(session, mode, info.market_id, info.outcome)
                    if closed:
                        pnl = round(sum(p.realized_pnl for p in closed), 6)
                        self.logs.log(
                            "INFO",
                            "settlement",
                            f"market resolved {info.outcome}; {len(closed)} position(s) settled, pnl {pnl:+.4f} USDC",
                            payload={
                                "market_id": info.market_id,
                                "outcome": info.outcome,
                                "positions": len(closed),
                                "pnl": pnl,
                                "mode": mode,
                            },
                            mode=mode,
                        )
                await self.orders.finalize_market(info.market_id, "paper", "market resolved")
                await self.orders.finalize_market(info.market_id, "live", "market resolved")
                await self._broadcast_state()
            elif info.market_id not in self._resolution_pending_logged:
                self._resolution_pending_logged.add(info.market_id)
                self.logs.log(
                    "WARNING",
                    "settlement",
                    f"market {info.market_id[:10]}… closed but outcome not published yet",
                    payload={"market_id": info.market_id},
                    mode=self._mode,
                )

    async def _reconcile_open_live_orders(self) -> None:
        with session_scope() as session:
            orders = session.execute(
                select(Order).where(
                    Order.mode == "live",
                    Order.status.in_(["SUBMITTED", "PARTIALLY_FILLED"]),
                    Order.exchange_order_id.is_not(None),
                )
            ).scalars().all()
            order_ids = [o.id for o in orders]
        for order_id in order_ids:
            try:
                await self.orders.reconcile_live_order(order_id)
            except Exception as exc:
                logger.warning("live reconciliation failed for order %s: %s", order_id, exc)

    async def _refresh_live_balance(self) -> None:
        balance = await self.live_engine.get_balance()
        if balance is None:
            return
        self._live_balance = balance
        self._live_balance_at = time.monotonic()
        try:
            with session_scope() as session:
                self.portfolio.set_cached_balance(session, "live", balance)
        except Exception as exc:
            logger.debug("live balance cache failed: %s", exc)

    async def _reconcile_live_positions(self) -> None:
        if self._mode != "live":
            return
        positions = await self.live_engine.fetch_onchain_positions()
        if positions is None:
            self.logs.log(
                "WARNING",
                "reconciliation",
                "on-chain live positions could not be fetched (funder address or data API unavailable)",
                mode="live",
            )
            return
        onchain = {}
        for item in positions:
            market_id = item.get("conditionId") or item.get("condition_id")
            outcome = str(item.get("outcome") or "").upper()
            try:
                size = float(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0.0
            if market_id and outcome in {"UP", "DOWN"}:
                onchain[(str(market_id), outcome)] = size
        with session_scope() as session:
            local = session.execute(
                select(Position).where(Position.mode == "live", Position.status == "open")
            ).scalars().all()
            local_map = {(p.market_id, p.side): p.size for p in local}
        mismatches = []
        for key, size in onchain.items():
            local_size = local_map.get(key, 0.0)
            if abs(local_size - size) > 0.01:
                mismatches.append({"market_id": key[0], "side": key[1], "local": local_size, "onchain": size})
        self.logs.log(
            "WARNING" if mismatches else "INFO",
            "reconciliation",
            f"live positions reconciled: {len(onchain)} on-chain, {len(local_map)} local, {len(mismatches)} mismatch(es)",
            payload={"mismatches": mismatches},
            mode="live",
        )

    # ------------------------------------------------------------------
    def live_readiness(self) -> dict:
        reasons = []
        if not self.settings.live_trading_enabled:
            reasons.append("LIVE_TRADING_ENABLED is false")
        if not self.settings.polymarket_geo_verified:
            reasons.append("geo eligibility not verified")
        if not self.settings.live_credentials_present:
            reasons.append("private key not configured")
        return {
            "live_enabled": self.settings.live_trading_enabled,
            "geo_verified": self.settings.polymarket_geo_verified,
            "credentials_present": self.settings.live_credentials_present,
            "funder_configured": bool(self.settings.polymarket_funder_address),
            "ready": not reasons,
            "reasons": reasons,
            "balance": self._live_balance,
        }

    async def snapshot(self) -> dict:
        config = self.runtime.get()
        market = self._market
        now_ms = int(time.time() * 1000)
        up_book = self.data.book_for("UP")
        down_book = self.data.book_for("DOWN")
        evaluation = (
            self.strategy.evaluate(market, self.data.books, now_ms, config) if market else None
        )
        self._evaluation = evaluation

        with session_scope() as session:
            balance = self._balance(session)
            account = self.portfolio.account(session, self._mode)
            positions = self.portfolio.open_positions(session, self._mode)
            marks = self._marks()
            unrealized = 0.0
            for position in positions:
                mark = marks.get((position.market_id, position.side))
                if mark is not None:
                    unrealized += position.size * mark - position.cost_basis - position.fees
                else:
                    unrealized += position.unrealized_pnl
            pnl = self.portfolio.pnl_summary(session, self._mode, unrealized)
            trades_today = self.portfolio.trades_today(session, self._mode)
            open_orders = session.execute(
                select(Order).where(
                    Order.mode == self._mode,
                    Order.status.in_(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]),
                )
            ).scalars().all()
            position_rows = [
                {
                    "id": p.id,
                    "market_id": p.market_id,
                    "side": p.side,
                    "size": p.size,
                    "avg_price": p.avg_price,
                    "cost_basis": p.cost_basis,
                    "fees": p.fees,
                    "unrealized_pnl": p.unrealized_pnl,
                    "realized_pnl": p.realized_pnl,
                    "status": p.status,
                    "resolution": p.resolution,
                    "opened_at": iso_z(p.opened_at),
                    "closed_at": iso_z(p.closed_at),
                }
                for p in positions
            ]
            order_rows = [
                {
                    "id": o.id,
                    "market_id": o.market_id,
                    "side": o.side,
                    "status": o.status,
                    "requested_notional": o.requested_notional,
                    "requested_price": o.requested_price,
                    "submitted_price": o.submitted_price,
                    "filled_size": o.filled_size,
                    "avg_fill_price": o.avg_fill_price,
                    "fee": o.fee,
                    "latency_ms": o.latency_ms,
                    "exchange_order_id": o.exchange_order_id,
                    "created_at": iso_z(o.created_at),
                }
                for o in open_orders
            ]

        latency_avg = round(sum(self._latencies) / len(self._latencies), 2) if self._latencies else None
        market_payload = None
        if market is not None:
            market_payload = {
                "market_id": market.market_id,
                "slug": market.slug,
                "event_slug": market.event_slug,
                "window_start": iso_z(market.window_start),
                "window_end": iso_z(market.window_end),
                "remaining_seconds": round(market.remaining_seconds(), 3),
                "duration_seconds": market.duration_seconds,
                "up_token_id": market.up_token_id,
                "down_token_id": market.down_token_id,
                "status": "active" if market.remaining_seconds() > 0 else "expired",
            }

        risk_halt = None
        if self._risk_halt_reason:
            risk_halt = self._risk_halt_reason
        elif pnl["daily_pnl"] <= -config.max_daily_loss and config.max_daily_loss > 0:
            risk_halt = "daily_loss_limit"
        elif config.daily_profit_target > 0 and pnl["daily_pnl"] >= config.daily_profit_target:
            risk_halt = "daily_profit_target"

        return {
            "server_time": iso_z(utcnow()),
            "bot": {
                "status": self._status,
                "mode": self._mode,
                "kill_switch": self._kill_switch,
                "started_at": iso_z(self._started_at),
                "risk_halt": risk_halt,
            },
            "market": market_payload,
            "books": {
                "up": up_book.snapshot() if up_book else None,
                "down": down_book.snapshot() if down_book else None,
            },
            "strategy": {
                "name": "FINAL_60_SECONDS_MARKET_BUY",
                **(
                    evaluation.summary()
                    if evaluation
                    else {
                        "in_window": False,
                        "remaining_seconds": None,
                        "up_ask": None,
                        "down_ask": None,
                        "up_armed": False,
                        "down_armed": False,
                        "reason": "no_market",
                    }
                ),
                "trigger_threshold": config.trigger_threshold,
                "final_window_seconds": config.final_window_seconds,
                "conflict_policy": config.conflict_policy,
                "duplicate_policy": config.duplicate_policy,
            },
            "account": {
                "mode": self._mode,
                "balance": round(balance, 6),
                "starting_balance": round(account.starting_balance, 6),
                **pnl,
                "trades_today": trades_today,
            },
            "latency": {
                "last_ms": self._latencies[-1] if self._latencies else None,
                "avg_ms": latency_avg,
                "samples": len(self._latencies),
            },
            "positions": position_rows,
            "open_orders": order_rows,
            "last_order": self._last_order,
            "market_data": self.data.status,
            "live": self.live_readiness(),
            "config": {
                **config.to_public_dict(),
                "trade_amount_usdc": config.trade_amount_usdc,
            },
        }

    async def analytics_summary(self, mode: str) -> dict:
        marks = self._marks()
        with session_scope() as session:
            positions = self.portfolio.open_positions(session, mode)
            unrealized = 0.0
            for position in positions:
                mark = marks.get((position.market_id, position.side))
                if mark is not None:
                    unrealized += position.size * mark - position.cost_basis - position.fees
                else:
                    unrealized += position.unrealized_pnl
            return self.analytics.summary(session, mode, unrealized)

    async def recent_orders(self, mode: str, limit: int = 100) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(
                select(Order).where(Order.mode == mode).order_by(Order.id.desc()).limit(limit)
            ).scalars().all()
            return [
                {
                    "id": o.id,
                    "market_id": o.market_id,
                    "side": o.side,
                    "status": o.status,
                    "requested_notional": o.requested_notional,
                    "requested_price": o.requested_price,
                    "submitted_price": o.submitted_price,
                    "filled_size": o.filled_size,
                    "avg_fill_price": o.avg_fill_price,
                    "fee": o.fee,
                    "latency_ms": o.latency_ms,
                    "exchange_order_id": o.exchange_order_id,
                    "error": o.error,
                    "reason": o.reason,
                    "created_at": iso_z(o.created_at),
                }
                for o in rows
            ]

    async def recent_positions(self, mode: str, limit: int = 100) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(
                select(Position)
                .where(Position.mode == mode)
                .order_by(Position.id.desc())
                .limit(limit)
            ).scalars().all()
            return [
                {
                    "id": p.id,
                    "market_id": p.market_id,
                    "side": p.side,
                    "size": p.size,
                    "avg_price": p.avg_price,
                    "cost_basis": p.cost_basis,
                    "fees": p.fees,
                    "unrealized_pnl": p.unrealized_pnl,
                    "realized_pnl": p.realized_pnl,
                    "status": p.status,
                    "resolution": p.resolution,
                    "opened_at": iso_z(p.opened_at),
                    "closed_at": iso_z(p.closed_at),
                }
                for p in rows
            ]

    async def recent_triggers(self, limit: int = 100) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(
                select(TriggerEvent).order_by(TriggerEvent.id.desc()).limit(limit)
            ).scalars().all()
            return [
                {
                    "id": t.id,
                    "market_id": t.market_id,
                    "side": t.side,
                    "best_ask": t.best_ask,
                    "ask_size": t.ask_size,
                    "remaining_seconds": t.remaining_seconds,
                    "threshold": t.threshold,
                    "triggered": t.triggered,
                    "decision": t.decision,
                    "reason": t.reason,
                    "created_at": iso_z(t.created_at),
                }
                for t in rows
            ]

    async def update_settings(self, changes: dict) -> dict:
        async with self._lock:
            with session_scope() as session:
                updated = self.runtime.update(session, changes)
            self.orders.reset_trigger_cache()
            self.logs.log(
                "WARNING",
                "settings",
                "strategy settings updated",
                payload=changes,
                mode=self._mode,
            )
        await self._broadcast_state()
        return updated.model_dump()

    async def reset_paper_account(self, balance: float) -> dict:
        if self._status == "RUNNING":
            raise BotError("stop the bot before resetting the paper account")
        if balance <= 0:
            raise BotError("balance must be positive")
        async with self._lock:
            with session_scope() as session:
                self.portfolio.reset_paper(session, balance)
            self.logs.log(
                "WARNING",
                "system",
                f"paper account reset to {balance:.2f} USDC",
                mode="paper",
            )
        await self._broadcast_state()
        return await self.snapshot()
