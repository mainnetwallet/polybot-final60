from datetime import datetime, timedelta, timezone

from app import clock
from app.market_discovery.types import MarketInfo


def _market(end: datetime) -> MarketInfo:
    return MarketInfo(
        market_id="m", slug="s", event_slug="e", series_id="x",
        window_start=end - timedelta(seconds=300), window_end=end,
        up_token_id="u", down_token_id="d",
    )


def test_remaining_uses_official_clock_offset():
    clock.reset()
    try:
        end = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=216)
        market = _market(end)
        base = market.remaining_seconds()
        assert 215 < base <= 216
        clock.set_offset_ms(-4000)  # PC clock 4 s ahead of official time -> more time left
        assert market.remaining_seconds() - base > 3.9
    finally:
        clock.reset()


def test_now_ms_applies_offset():
    clock.reset()
    try:
        clock.set_offset_ms(2500)
        import time
        assert abs(clock.now_ms() - (time.time() * 1000 + 2500)) < 50
    finally:
        clock.reset()
