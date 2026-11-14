from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.config.runtime import RuntimeSettings
from app.config.settings import get_settings
from app.database.session import session_scope
from app.execution.base import ExecutionEngine, ExecutionReport
from app.market_data.book import OrderBook
from app.market_discovery.types import MarketInfo
from app.monitoring.logs import LogService
from app.order_manager.manager import OrderManager
from app.paper_trading.engine import PaperExecutionEngine
from app.portfolio.service import Portfolio
from app.risk_manager.manager import RiskContext, RiskManager
from app.strategy.engine import FinalWindowStrategy


def make_market(
    remaining: float = 55.0,
    duration: float = 300.0,
    market_id: str = "0xmarket",
    slug: str = "btc-updown-5m-test",
) -> MarketInfo:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end = now + timedelta(seconds=remaining)
    start = end - timedelta(seconds=duration)
    return MarketInfo(
        market_id=market_id,
        slug=slug,
        event_slug=slug,
        series_id="10684",
        window_start=start,
        window_end=end,
        up_token_id="up-token",
        down_token_id="down-token",
        tick_size=0.01,
        min_order_size=5.0,
    )


def make_book(
    token_id: str,
    side: str,
    asks: list[tuple[float, float]],
    bids: list[tuple[float, float]] | None = None,
    age_ms: int = 0,
    market_id: str = "0xmarket",
) -> OrderBook:
    book = OrderBook(token_id=token_id, market_id=market_id, side=side)
    book.asks = {round(p, 6): s for p, s in asks}
    book.bids = {round(p, 6): s for p, s in (bids if bids is not None else [(0.5, 100.0)])}
    book.updated_at_ms = int(time.time() * 1000) - age_ms
    return book


def make_config(**overrides) -> RuntimeSettings:
    values = dict(
        trigger_threshold=0.80,
        final_window_seconds=60,
        max_execution_price=0.99,
        trade_amount_usdc=5.0,
        max_daily_loss=5.0,
        daily_profit_target=10.0,
        stale_data_max_age_ms=2000,
        duplicate_policy="one_per_market",
        conflict_policy="up_first",
        paper_slippage_bps=0.0,
        paper_fee_rate=0.07,
        max_trades_per_day=60,
        max_open_positions=2,
        paper_fill_ratio=1.0,
        paper_fee_exponent=1.0,
        paper_starting_balance=10.0,
        live_trading_enabled=False,
        live_credentials_present=False,
        geo_verified=False,
        # The app defaults moved on (stage 0 min ask .69 / gap 70, stage 1 take-profit 8 % / sell .98);
        # the behaviour tests keep the values they were written against and set their own where it matters.
        stage0_min_ask=0.60,
        stage0_min_price_gap_usd=80.0,
        stage1_take_profit_pct=10.0,
        stage1_sell_price_high=0.99,
    )
    values.update(overrides)
    return RuntimeSettings(**values)


class StubRuntime:
    def __init__(self, config: RuntimeSettings) -> None:
        self.config = config

    def get(self) -> RuntimeSettings:
        return self.config


class LiveSpy(ExecutionEngine):
    mode = "live"

    def __init__(self) -> None:
        self.calls = 0

    async def place_buy(self, request):
        self.calls += 1
        return ExecutionReport(status="REJECTED", error="live never allowed in paper tests")

    async def get_balance(self) -> float | None:
        return 100.0


def make_ctx(
    balance: float = 10.0,
    daily_pnl: float = 0.0,
    trades_today: int = 0,
    open_positions: int = 0,
    remaining: float = 55.0,
    kill_switch: bool = False,
    exposed_sides: frozenset[str] = frozenset(),
    btc_price: float | None = None,
    price_to_beat: float | None = None,
    price_gap_issue: str = "",
    stage1_bought: bool = False,
    stage1_remaining: float | None = None,
    stage0_pure_sides: frozenset[str] = frozenset(),
) -> RiskContext:
    return RiskContext(
        now_ms=int(time.time() * 1000),
        kill_switch=kill_switch,
        account_balance=balance,
        daily_pnl=daily_pnl,
        trades_today=trades_today,
        open_positions=open_positions,
        market_active=True,
        market_expired=remaining <= 0,
        remaining_seconds=remaining,
        exposed_sides=exposed_sides,
        btc_price=btc_price,
        price_to_beat=price_to_beat,
        price_gap_issue=price_gap_issue,
        stage1_bought=stage1_bought,
        stage1_remaining=stage1_remaining,
        stage0_pure_sides=stage0_pure_sides,
    )


def build_stack(config: RuntimeSettings | None = None):
    config = config or make_config()
    settings = get_settings()
    runtime = StubRuntime(config)
    portfolio = Portfolio(settings)
    with session_scope() as session:
        portfolio.ensure_accounts(session)
        account = portfolio.account(session, "paper")
        account.balance = config.paper_starting_balance
        account.starting_balance = config.paper_starting_balance
    live_spy = LiveSpy()
    books_holder: dict[str, OrderBook] = {}

    def provider(mode: str) -> ExecutionEngine:
        if mode == "live":
            return live_spy
        return PaperExecutionEngine(settings, lambda side: books_holder.get(side))

    manager = OrderManager(
        settings, runtime, portfolio, RiskManager(), provider, LogService()
    )
    strategy = FinalWindowStrategy()
    return {
        "manager": manager,
        "strategy": strategy,
        "portfolio": portfolio,
        "settings": settings,
        "config": config,
        "runtime": runtime,
        "live_spy": live_spy,
        "books": books_holder,
    }


def books_for(
    market: MarketInfo,
    up_ask: float,
    down_ask: float,
    size: float = 1000.0,
    up_size: float | None = None,
    down_size: float | None = None,
    up_age_ms: int = 0,
):
    """Token-keyed books, exactly as the websocket client stores them."""
    return {
        market.up_token_id: make_book(
            market.up_token_id,
            "UP",
            [(up_ask, up_size if up_size is not None else size)],
            age_ms=up_age_ms,
            market_id=market.market_id,
        ),
        market.down_token_id: make_book(
            market.down_token_id,
            "DOWN",
            [(down_ask, down_size if down_size is not None else size)],
            market_id=market.market_id,
        ),
    }


def stage_books(stack, market, books):
    """Register token-keyed books for the strategy and side-keyed books for paper fills."""
    stack["books"]["UP"] = books[market.up_token_id]
    stack["books"]["DOWN"] = books[market.down_token_id]
