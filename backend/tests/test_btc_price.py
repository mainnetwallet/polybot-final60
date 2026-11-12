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


def test_price_to_beat_is_the_rtds_tick_at_window_start_until_gamma_answers():
    start = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    start_ms = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    feed = BtcPriceFeed(Settings())
    market = _market(start)
    feed.handle_message(_frame(start_ms - 5_000, 70_900.0))  # before the window: never used
    feed.handle_message(_frame(start_ms + 100, 71_000.0))  # official TWAP60 tick at the window start
    feed.handle_message(_frame(start_ms + 3_000, 71_040.0))
    snap = feed.snapshot(market)
    assert snap["price"] == 71_040.0
    assert snap["price_to_beat"] == 71_000.0
    assert snap["price_to_beat_source"] == "polymarket-rtds"
    assert snap["diff"] == 40.0

    # Gamma's official value replaces it once published
    feed._opens[market.market_id] = (71_001.25, "polymarket")
    snap = feed.snapshot(market)
    assert snap["price_to_beat"] == 71_001.25 and snap["price_to_beat_source"] == "polymarket"


def test_no_price_to_beat_without_a_tick_at_the_window_start():
    start = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    start_ms = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(start_ms + 30_000, 71_000.0))  # started mid-window: nothing is guessed
    snap = feed.snapshot(_market(start))
    assert snap["price_to_beat"] is None and snap["price_to_beat_source"] is None and snap["diff"] is None


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
    feed._opens[market.market_id] = (71_010.5, "polymarket")  # what the loop stores once Polymarket answers

    snap = feed.snapshot(market)
    assert snap["price_to_beat"] == 71_010.5
    assert snap["price_to_beat_source"] == "polymarket"
    assert round(snap["diff"], 2) == 39.5


async def test_price_to_beat_comes_from_gamma_event_metadata_no_fallback():
    start = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    market = _market(start)
    feed = BtcPriceFeed(Settings())

    # Gamma publishes the TWAP-based price to beat; no other endpoint is consulted
    feed._http = _FakeHttp({"/events": [{"eventMetadata": {"priceToBeat": 70_123.45}}], "crypto-price": {"openPrice": 70_222.0}})
    assert await feed._fetch_open_price(market) == (70_123.45, "polymarket")
    assert not any("crypto-price" in url for url in feed._http.calls)

    # metadata as a JSON string / on the market object / numeric string are all accepted
    feed._http = _FakeHttp({"/events": [{"markets": [{"eventMetadata": json.dumps({"priceToBeat": "70123.5"})}]}]})
    assert await feed._fetch_open_price(market) == (70_123.5, "polymarket")

    # not published yet: nothing is shown, no substitution
    feed._http = _FakeHttp({"/events": [{"eventMetadata": {"priceToBeat": None}}], "crypto-price": {"openPrice": 70_222.0}})
    assert await feed._fetch_open_price(market) is None
    assert feed.snapshot(market)["price_to_beat"] is None
    assert feed.snapshot(market)["price_to_beat_error"]

    feed._http = _FakeHttp({"/events": RuntimeError("down")})
    assert await feed._fetch_open_price(market) is None
    assert "down" in feed.snapshot(market)["price_to_beat_error"]


def test_current_price_stream_is_the_twap60_topic():
    sub = json.loads(BtcPriceFeed._subscribe_frame(0))["subscriptions"][0]
    assert sub["topic"] == "crypto_prices_twap_sixty"
    assert BtcPriceFeed(Settings()).snapshot(None)["price_source"] == "chainlink-twap60"


def test_subscription_frames():
    def sub(mode):
        return json.loads(BtcPriceFeed._subscribe_frame(mode))["subscriptions"][0]

    assert sub(0)["topic"] == "crypto_prices_twap_sixty" and "filters" not in sub(0)  # default: unfiltered
    assert "filters" not in sub(1)
    assert json.loads(sub(2)["filters"]) == {"symbol": "btc/usd"}
    assert "filters" in sub(3)
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(5, 3000.0, symbol="eth/usd"))  # unfiltered stream: other symbols ignored
    feed.handle_message(_frame(6, 71_111.0))
    assert feed.latest == (6, 71_111.0)


def test_history_batch_with_any_topic_label_is_parsed():
    feed = BtcPriceFeed(Settings())
    batch = {"payload": {"data": [{"timestamp": 1000, "value": 71_000.5}, {"timestamp": 2000, "value": 71_001.5}], "symbol": "btc/usd"}}
    feed.handle_message(json.dumps(batch))
    assert feed.latest == (2000, 71_001.5)
