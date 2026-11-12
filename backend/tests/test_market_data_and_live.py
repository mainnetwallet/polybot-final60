from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config.settings import Settings
from app.execution.base import OrderRequest
from app.live_trading.engine import LiveExecutionEngine
from app.market_data.book import OrderBook
from app.market_data.client import ClobMarketDataClient
from app.market_discovery.service import MarketDiscovery
from app.market_discovery.types import MarketInfo


def _market() -> MarketInfo:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return MarketInfo(
        market_id="0xm", slug="s", event_slug="s", series_id="1",
        window_start=now, window_end=now, up_token_id="UP", down_token_id="DOWN",
    )


def _client():
    seen = []

    async def on_book(market_id, side, book):
        seen.append((side, book.best_bid, book.best_ask))

    client = ClobMarketDataClient(Settings(), on_book)
    client.set_market(_market())
    return client, seen


def test_book_ignores_zero_size_levels():
    book = OrderBook(token_id="t", market_id="m", side="UP", asks={0.5: 0.0, 0.8: 10.0}, bids={0.9: 0.0, 0.7: 5.0})
    assert book.best_ask == 0.8
    assert book.best_bid == 0.7


def test_price_change_official_format_updates_and_prunes():
    client, seen = _client()

    async def run():
        await client._handle_message({
            "event_type": "book", "asset_id": "UP",
            "bids": [{"price": "0.70", "size": "5"}],
            "asks": [{"price": "0.75", "size": "9"}, {"price": "0.81", "size": "4"}],
        })
        # new better ask, then the 0.75 level is reported gone via best_ask 0.81
        await client._handle_message({
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "UP", "price": "0.75", "size": "0", "side": "SELL", "best_bid": "0.70", "best_ask": "0.81"},
            ],
        })

    asyncio.run(run())
    book = client.books["UP"]
    assert book.best_ask == 0.81
    assert book.ask_size == 4.0
    assert seen[-1] == ("UP", 0.70, 0.81)


def test_price_change_stale_best_is_pruned_without_fake_levels():
    client, _ = _client()

    async def run():
        await client._handle_message({
            "event_type": "book", "asset_id": "DOWN",
            "bids": [{"price": "0.10", "size": "5"}],
            "asks": [{"price": "0.20", "size": "9"}, {"price": "0.25", "size": "3"}],
        })
        await client._handle_message({
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "DOWN", "price": "0.30", "size": "7", "side": "SELL", "best_bid": "0.10", "best_ask": "0.25"},
            ],
        })

    asyncio.run(run())
    book = client.books["DOWN"]
    assert 0.20 not in book.asks  # stale level pruned by reported best_ask
    assert book.best_ask == 0.25
    assert all(size > 0 for size in book.asks.values())


class _FakeV2Client:
    def __init__(self, response):
        self.response = response
        self.args = None

    def create_and_post_market_order(self, args, options, order_type):
        self.args = args
        return self.response


def _request(notional=5.0, max_price=0.99):
    return OrderRequest(
        order_key="k", client_order_id="c", market_id="m", side="UP", token_id="tok",
        notional=notional, max_price=max_price, observed_at_ms=0,
    )


def test_live_matched_fill_uses_real_amounts_not_limit_price():
    engine = LiveExecutionEngine(Settings())
    client = _FakeV2Client({
        "success": True, "errorMsg": "", "orderID": "0xabc", "status": "matched",
        "makingAmount": "5", "takingAmount": "6.25",
    })
    report = engine._place_sync(client, _request())
    assert report.status == "FILLED"
    assert report.filled_size == 6.25
    assert report.avg_price == 0.8  # 5 / 6.25, not the 0.99 cap
    assert client.args.price == 0.99 and client.args.amount == 5.0


def test_live_amounts_in_base_units_are_normalised():
    engine = LiveExecutionEngine(Settings())
    client = _FakeV2Client({
        "success": True, "orderID": "0xabc", "status": "matched",
        "makingAmount": "5000000", "takingAmount": "6250000",
    })
    report = engine._place_sync(client, _request())
    assert report.filled_size == 6.25 and report.avg_price == 0.8


def test_live_fok_kill_is_canceled_not_filled():
    engine = LiveExecutionEngine(Settings())
    client = _FakeV2Client({"success": False, "errorMsg": "FOK orders are fully filled or killed", "orderID": ""})
    report = engine._place_sync(client, _request())
    assert report.status == "CANCELED" and report.filled_size == 0


def test_live_cancel_all_counts_list():
    engine = LiveExecutionEngine(Settings())

    class C:
        def cancel_all(self):
            return {"canceled": ["a", "b"], "not_canceled": {}}

    engine._client = C()
    assert asyncio.run(engine.cancel_open_orders()) == 2


def test_discovery_slug_gives_exact_window_start():
    start = MarketDiscovery._start_from_slug("btc-updown-5m-1760000100")
    assert start == datetime(2025, 10, 9, 8, 55, 0)
    assert MarketDiscovery._start_from_slug("no-number-here") is None


class _FeeV2Client(_FakeV2Client):
    """Fake CLOB V2 client that also serves the market's fee parameters (``fd``)."""

    def __init__(self, response, fd):
        super().__init__(response)
        self.fd = fd
        self.lookups = 0

    def get_clob_market_info(self, condition_id):
        self.lookups += 1
        return {"t": [{"t": "tok"}], "fd": self.fd}


def test_live_fill_records_taker_fee_from_market_parameters():
    engine = LiveExecutionEngine(Settings())
    client = _FeeV2Client(
        {
            "success": True, "orderID": "0xabc", "status": "matched",
            "makingAmount": "5", "takingAmount": "6.25",
        },
        {"r": 0.07, "e": 1},
    )
    report = engine._place_sync(client, _request())
    # docs.polymarket.com/trading/fees: 6.25 shares * 0.07 * 0.8 * 0.2 = 0.07 USDC
    assert report.fee == 0.07
    assert report.fills[0].fee == 0.07
    assert client.lookups == 1
    # second fill on the same token reuses the cached parameters
    engine._place_sync(client, _request())
    assert client.lookups == 1


def test_live_fee_is_zero_for_fee_free_market():
    engine = LiveExecutionEngine(Settings())
    client = _FeeV2Client(
        {
            "success": True, "orderID": "0xabc", "status": "matched",
            "makingAmount": "5", "takingAmount": "6.25",
        },
        {"r": 0, "e": 1},
    )
    assert engine._place_sync(client, _request()).fee == 0.0


def test_live_fee_falls_back_to_documented_rate_when_market_info_missing():
    engine = LiveExecutionEngine(Settings())
    # _FakeV2Client has no get_clob_market_info: the lookup fails quietly
    client = _FakeV2Client(
        {
            "success": True, "orderID": "0xabc", "status": "matched",
            "makingAmount": "5", "takingAmount": "6.25",
        }
    )
    report = engine._place_sync(client, _request())
    assert report.fee == engine.estimate_fee("tok", 6.25, 0.8) == 0.07


def test_live_fee_matches_docs_table_and_rounds_to_five_decimals():
    engine = LiveExecutionEngine(Settings())
    engine._fee_params["tok"] = (0.07, 1.0)
    assert engine.estimate_fee("tok", 100, 0.80) == 1.12  # docs fee table, crypto, $0.80
    assert engine.estimate_fee("tok", 100, 0.50) == 1.75
    assert engine.estimate_fee("tok", 100, 0.99) == 0.0693  # docs table shows 0.07 (2-dp rounding)
    assert engine.estimate_fee("tok", 0.001, 0.99) == 0.0  # below 0.00001 rounds to zero
