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


def test_price_to_beat_uses_tick_nearest_window_start():
    start = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    start_ms = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(start_ms - 1000, 71_000.0))
    feed.handle_message(_frame(start_ms + 400, 71_639.36))
    feed.handle_message(_frame(start_ms + 5000, 71_478.86))
    market = _market(start)

    snap = feed.snapshot(market)
    assert snap["price_to_beat"] == 71_639.36
    assert snap["price"] == 71_478.86
    assert round(snap["diff"], 2) == -160.5
    assert snap["price_to_beat_source"] == "feed"


def test_no_price_to_beat_without_opening_tick():
    start = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None) - timedelta(seconds=120)
    feed = BtcPriceFeed(Settings())
    feed.handle_message(_frame(int(time.time() * 1000), 70_000.0))
    snap = feed.snapshot(_market(start))
    assert snap["price"] == 70_000.0
    assert snap["price_to_beat"] is None
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
