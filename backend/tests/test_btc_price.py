from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from app.config.settings import Settings
from app.market_data.btc_price import BtcPriceFeed
from app.market_discovery.types import MarketInfo


def _market(start: datetime) -> MarketInfo:
    return MarketInfo(
        market_id="m1",
        slug="btc-updown-5m-x",
        event_slug="btc-updown-5m-x",
        series_id="s",
        window_start=start,
        window_end=start + timedelta(seconds=300),
        up_token_id="u",
        down_token_id="d",
    )


def _frame(ts_ms: int, value: float, symbol: str = "btc/usd") -> str:
    return json.dumps(
        {
            "topic": "crypto_prices_chainlink",
            "type": "update",
            "payload": {"symbol": symbol, "timestamp": ts_ms, "value": value},
        }
    )


def test_current_price_comes_from_latest_stream_tick():
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(1_000, 71_000.0))
    feed.handle_message(_frame(2_000, 71_050.5))
    feed.handle_message(_frame(1_500, 1.0))  # out-of-order tick is ignored
    assert feed.latest == (2_000, 71_050.5)
    assert feed.snapshot(None)["price"] == 71_050.5


def test_price_to_beat_is_never_estimated_from_the_stream():
    start = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    start_ms = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(start_ms + 100, 71_000.0))  # tick exactly at the window start
    snap = feed.snapshot(_market(start))
    assert snap["price"] == 71_000.0
    assert snap["price_to_beat"] is None
    assert snap["price_to_beat_source"] is None
    assert snap["diff"] is None


def test_ignores_other_symbols_and_bad_frames():
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(1, 3000.0, symbol="eth/usd"))
    feed.handle_message("not json")
    feed.handle_message(json.dumps({"topic": "crypto_prices_chainlink", "payload": {"symbol": "btc/usd", "value": "x"}}))
    assert feed.latest is None


def test_initial_batch_is_ingested():
    feed = BtcPriceFeed(Settings())
    feed.handle_message(
        json.dumps(
            {
                "topic": "crypto_prices_chainlink",
                "payload": {"symbol": "btc/usd", "data": [{"timestamp": 1, "value": 1.5}, {"timestamp": 2, "value": 2.5}]},
            }
        )
    )
    assert feed.latest == (2, 2.5)


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append(url)
        for key, data in self.routes.items():
            if key in url:
                if isinstance(data, Exception):
                    raise data
                return _Resp(data)
        raise RuntimeError("no route")


def test_official_price_to_beat_and_gap():
    start = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(int(time.time() * 1000), 71_050.0))
    market = _market(start)
    feed._opens[market.market_id] = (71_010.5, "gamma")  # what the loop stores once Polymarket answers

    snap = feed.snapshot(market)
    assert snap["price_to_beat"] == 71_010.5
    assert snap["price_to_beat_source"] == "gamma"
    assert round(snap["diff"], 2) == 39.5


async def test_fetch_prefers_website_endpoint_then_gamma():
    start = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    market = _market(start)
    feed = BtcPriceFeed(Settings())

    both = {"/events": [{"eventMetadata": {"priceToBeat": 70_123.45}}], "crypto-price": {"openPrice": 70_222.0}}
    feed._http = _FakeHttp(both)
    assert await feed._fetch_open_price(market) == (70_222.0, "polymarket")

    feed._http = _FakeHttp({"/events": [{"eventMetadata": {"priceToBeat": 70_123.45}}], "crypto-price": {"openPrice": None}})
    assert await feed._fetch_open_price(market) == (70_123.45, "gamma")
    assert await feed._fetch_open_price(market, allow_gamma=False) is None

    feed._http = _FakeHttp({"/events": RuntimeError("down"), "crypto-price": RuntimeError("down")})
    assert await feed._fetch_open_price(market) is None


def test_subscription_frames():
    filtered = json.loads(BtcPriceFeed._subscribe_frame(0))
    assert filtered["subscriptions"][0]["topic"] == "crypto_prices_chainlink"
    assert json.loads(filtered["subscriptions"][0]["filters"]) == {"symbol": "btc/usd"}
    unfiltered = json.loads(BtcPriceFeed._subscribe_frame(1))
    assert "filters" not in unfiltered["subscriptions"][0]
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(5, 3000.0, symbol="eth/usd"))  # unfiltered stream: other symbols ignored
    feed.handle_message(_frame(6, 71_111.0))
    assert feed.latest == (6, 71_111.0)
