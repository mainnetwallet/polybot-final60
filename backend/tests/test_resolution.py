"""Regression tests: a finished 5m market must settle, never stay 'Unrealized'."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config.settings import get_settings
from app.market_discovery.service import MarketDiscovery, MarketMonitor
from app.market_discovery.types import ResolutionInfo
from tests.helpers import make_market


def _finished_market(slug="btc-updown-5m-done", market_id="0xdone"):
    return make_market(remaining=-30.0, market_id=market_id, slug=slug)


class _FakeDiscovery:
    """Returns scripted resolution answers, one per poll."""

    def __init__(self, market_id, answers):
        self.market_id = market_id
        self.answers = list(answers)
        self.calls = 0

    async def get_resolution(self, market):
        self.calls += 1
        closed, outcome = self.answers.pop(0) if self.answers else self.answers_last
        return ResolutionInfo(self.market_id, closed, outcome)


@pytest.mark.asyncio
async def test_closed_without_winner_is_kept_and_settles_on_later_poll():
    market = _finished_market()
    fake = _FakeDiscovery(market.market_id, [(True, None), (True, None), (True, "UP")])
    monitor = MarketMonitor(fake, get_settings(), on_market=None)
    monitor.track(market)

    first = await monitor.poll_resolution()
    assert [r.outcome for r in first] == [None]
    assert monitor.is_tracked(market.slug), "closed-but-unresolved market must stay tracked"

    second = await monitor.poll_resolution()
    assert [r.outcome for r in second] == [None]
    assert monitor.is_tracked(market.slug)

    third = await monitor.poll_resolution()
    assert [r.outcome for r in third] == ["UP"]
    assert not monitor.is_tracked(market.slug)


@pytest.mark.asyncio
async def test_running_market_is_not_polled():
    market = make_market(remaining=30.0)
    fake = _FakeDiscovery(market.market_id, [(True, "UP")])
    monitor = MarketMonitor(fake, get_settings(), on_market=None)
    monitor.track(market)
    assert await monitor.poll_resolution() == []
    assert fake.calls == 0


def _discovery_with(handlers):
    settings = get_settings()
    discovery = MarketDiscovery(settings)
    discovery._clob = httpx.AsyncClient(
        base_url=settings.clob_base_url, transport=httpx.MockTransport(handlers["clob"])
    )
    discovery._client = httpx.AsyncClient(
        base_url=settings.gamma_base_url, transport=httpx.MockTransport(handlers["gamma"])
    )
    return discovery


@pytest.mark.asyncio
async def test_clob_winner_flag_resolves_market():
    market = _finished_market()

    def clob(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/markets/{market.market_id}"
        return httpx.Response(
            200,
            json={
                "closed": True,
                "tokens": [
                    {"token_id": market.up_token_id, "outcome": "Up", "price": 0, "winner": False},
                    {"token_id": market.down_token_id, "outcome": "Down", "price": 1, "winner": True},
                ],
            },
        )

    discovery = _discovery_with({"clob": clob, "gamma": lambda r: httpx.Response(500)})
    info = await discovery.get_resolution(market)
    assert (info.closed, info.outcome) == (True, "DOWN")
    await discovery.close()


@pytest.mark.asyncio
async def test_gamma_fallback_needs_terminal_prices_not_just_closed():
    market = _finished_market()

    def clob(request):
        return httpx.Response(200, json={"closed": True, "tokens": []})

    def gamma_factory(prices):
        def gamma(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "markets": [
                            {
                                "closed": True,
                                "outcomes": json.dumps(["Up", "Down"]),
                                "outcomePrices": json.dumps(prices),
                            }
                        ]
                    }
                ],
            )

        return gamma

    pending = _discovery_with({"clob": clob, "gamma": gamma_factory(["0.55", "0.45"])})
    info = await pending.get_resolution(market)
    assert (info.closed, info.outcome) == (True, None)
    await pending.close()

    done = _discovery_with({"clob": clob, "gamma": gamma_factory(["1", "0"])})
    info = await done.get_resolution(market)
    assert (info.closed, info.outcome) == (True, "UP")
    await done.close()


# ---------------------------------------------------------------------------
# Bot-level: the exact symptom (finished 5m trade stuck "Unrealized", balance
# never credited), including recovery after a restart.
# ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402

from app.config.runtime import SettingsService  # noqa: E402
from app.database.models import Market, utcnow  # noqa: E402
from app.database.session import session_scope  # noqa: E402
from app.execution.base import ExecutionReport, FillPart  # noqa: E402
from app.monitoring.logs import NullPublisher  # noqa: E402
from app.portfolio.analytics import AnalyticsService  # noqa: E402
from app.portfolio.service import Portfolio  # noqa: E402
from app.services.bot import TradingBot  # noqa: E402
from app.database.models import Order  # noqa: E402


def _bot_with_paper_position(market):
    settings = get_settings()
    portfolio = Portfolio(settings)
    bot = TradingBot(settings, SettingsService(settings), portfolio, AnalyticsService(), NullPublisher())
    with session_scope() as session:
        portfolio.ensure_accounts(session)
        account = portfolio.account(session, "paper")
        account.balance = 10.0
        account.starting_balance = 10.0
        session.add(
            Market(
                market_id=market.market_id,
                slug=market.slug,
                event_slug=market.event_slug,
                series_id=market.series_id,
                window_start=market.window_start,
                window_end=market.window_end,
                up_token_id=market.up_token_id,
                down_token_id=market.down_token_id,
            )
        )
        order = Order(
            order_key="k1", client_order_id="c1", mode="paper", market_id=market.market_id,
            side="UP", token_id=market.up_token_id, status="FILLED",
            requested_notional=4.0, requested_price=0.8,
        )
        session.add(order)
        session.flush()
        portfolio.apply_report(
            session, "paper", order,
            ExecutionReport(
                status="FILLED", filled_size=5.0, avg_price=0.8, fee=0.0,
                fills=[FillPart(price=0.8, size=5.0, fee=0.0)],
            ),
        )
    return bot, portfolio


@pytest.mark.asyncio
async def test_finished_market_settles_and_credits_balance_even_after_restart():
    market = _finished_market(slug="btc-updown-5m-restart", market_id="0xrestart")
    bot, portfolio = _bot_with_paper_position(market)
    with session_scope() as session:
        assert portfolio.balance(session, "paper") == 6.0  # 10 - 4 cost

    # Fresh bot == restarted process: monitor has NO in-memory knowledge of the market.
    assert not bot.monitor.is_tracked(market.slug)

    answers = [(True, None), (True, "UP")]  # winner published a poll later

    async def scripted(_market):
        closed, outcome = answers.pop(0)
        return ResolutionInfo(market.market_id, closed, outcome)

    bot.discovery.get_resolution = scripted

    await bot._poll_resolutions()  # closed, winner not published yet
    snap = await bot.snapshot()
    assert snap["positions"][0]["awaiting_settlement"] is True
    assert snap["account"]["positions_awaiting_settlement"] == 1
    with session_scope() as session:
        assert portfolio.balance(session, "paper") == 6.0

    await bot._poll_resolutions()  # winner published -> settle + credit
    with session_scope() as session:
        assert portfolio.balance(session, "paper") == 11.0  # 6 + 5 winning shares x $1
        assert portfolio.open_positions(session, "paper") == []
        assert portfolio.realized_today(session, "paper") == 1.0
    snap = await bot.snapshot()
    assert snap["positions"] == []
    assert snap["account"]["balance"] == 11.0


@pytest.mark.asyncio
async def test_live_win_is_redeemed_then_balance_refreshed():
    market = _finished_market(slug="btc-updown-5m-redeem", market_id="0xredeem")
    bot, _ = _bot_with_paper_position(market)
    bot.settings.live_auto_redeem = True
    calls = []

    class FakeLive:
        async def redeem_market(self, condition_id):
            calls.append(condition_id)
            if len(calls) == 1:
                return {"status": "not_redeemable_yet"}
            return {"status": "redeemed", "transaction_hash": "0xabc"}

        async def get_balance(self):
            return 15.5

    bot.live_engine = FakeLive()
    try:
        bot._queue_live_redeem(market.market_id)
        await bot._process_redeem_queue()  # not redeemable yet -> stays queued
        assert market.market_id in bot._redeem_queue
        bot._redeem_queue[market.market_id]["next_at"] = 0.0
        await bot._process_redeem_queue()  # redeemed
        assert bot._redeem_queue == {}
        assert bot._live_balance == 15.5
    finally:
        bot.settings.live_auto_redeem = False


def test_live_win_without_auto_redeem_only_warns():
    market = _finished_market(slug="btc-updown-5m-noredeem", market_id="0xnoredeem")
    bot, _ = _bot_with_paper_position(market)
    bot._queue_live_redeem(market.market_id)
    assert bot._redeem_queue == {}
    assert market.market_id in bot._redeem_notice_logged
