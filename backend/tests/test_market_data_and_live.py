from __future__ import annotations

import asyncio

import pytest
from datetime import datetime, timezone, timedelta

from app.config.settings import Settings
from app.execution.base import OrderRequest
from app.live_trading.engine import LiveExecutionEngine, LiveTradingUnavailable
from app.market_data.book import OrderBook
from app.market_data.client import ClobMarketDataClient
from app.market_discovery.service import DiscoveryError, MarketDiscovery
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
        self.defer_exec = False

    def create_and_post_market_order(self, args, options, order_type, defer_exec=False):
        self.args = args
        self.defer_exec = defer_exec
        return self.response


def _request(notional=5.0, max_price=0.99, asset_version=1):
    return OrderRequest(
        order_key="k", client_order_id="c", market_id="m", side="UP", token_id="tok",
        notional=notional, max_price=max_price, observed_at_ms=0,
        asset_version=asset_version,
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
    assert client.defer_exec is True


def test_live_amounts_in_base_units_are_normalised():
    engine = LiveExecutionEngine(Settings())
    client = _FakeV2Client({
        "success": True, "orderID": "0xabc", "status": "matched",
        "makingAmount": "5000000", "takingAmount": "6250000",
    })
    report = engine._place_sync(client, _request())
    assert report.filled_size == 6.25 and report.avg_price == 0.8


def test_protocol_v2_market_order_uses_position_id():
    engine = LiveExecutionEngine(Settings())
    client = _FakeV2Client({
        "success": True, "orderID": "0xabc", "status": "matched",
        "makingAmount": "5", "takingAmount": "6.25",
    })
    report = engine._place_sync(client, _request(asset_version=2))
    assert report.status == "FILLED"
    assert client.args.token_id is None
    assert client.args.position_id == "tok"


def test_live_delayed_order_does_not_poll_inline():
    engine = LiveExecutionEngine(Settings())

    class Delayed(_FakeV2Client):
        def get_order(self, _order_id):
            raise AssertionError("order polling must run on maintenance, not the book callback")

    client = Delayed({"success": True, "orderID": "0xabc", "status": "delayed"})
    report = engine._place_sync(client, _request())
    assert report.status == "SUBMITTED"
    assert report.exchange_order_id == "0xabc"


def test_live_post_exception_is_kept_active_for_safe_reconciliation():
    engine = LiveExecutionEngine(Settings())

    class Unknown(_FakeV2Client):
        def create_and_post_market_order(self, *args, **kwargs):
            raise TimeoutError("transport timeout")

    report = engine._place_sync(Unknown(None), _request())
    assert report.status == "SUBMITTED"
    assert report.exchange_order_id is None
    assert "unknown" in report.error


def test_live_fok_kill_is_canceled_not_filled():
    engine = LiveExecutionEngine(Settings())
    client = _FakeV2Client({"success": False, "errorMsg": "FOK orders are fully filled or killed", "orderID": ""})
    report = engine._place_sync(client, _request())
    assert report.status == "CANCELED" and report.filled_size == 0


def test_live_cancel_targets_only_the_tracked_order():
    engine = LiveExecutionEngine(Settings())

    class C:
        def __init__(self):
            self.order_id = None

        def cancel_order(self, payload):
            self.order_id = payload.orderID
            return {"canceled": [payload.orderID], "not_canceled": {}}

    client = C()
    engine._client = client
    assert asyncio.run(engine.cancel_order("only-this-order")) is True
    assert client.order_id == "only-this-order"


def test_live_validation_errors_do_not_return_exception_text():
    engine = LiveExecutionEngine(Settings())

    def fail_with_secret():
        raise RuntimeError("private-key-material")

    engine._build_client = fail_with_secret

    try:
        asyncio.run(engine.validate())
    except Exception as exc:
        assert "private-key-material" not in str(exc)
        assert "RuntimeError" in str(exc)
    else:
        raise AssertionError("validation should fail")


def test_live_validation_requires_a_positive_collateral_allowance():
    engine = LiveExecutionEngine(Settings())
    engine._get_client = lambda: _ready_client()
    engine._balance_sync = lambda _client: 10.0
    engine._allowance_sync = lambda _client: 0.0

    async def run():
        try:
            await engine.validate()
        except Exception as exc:
            assert "allowance" in str(exc).lower()
        else:
            raise AssertionError("zero collateral allowance must fail validation")

    async def _ready_client():
        return object()

    asyncio.run(run())


def test_discovery_slug_gives_exact_window_start():
    start = MarketDiscovery._start_from_slug("btc-updown-5m-1760000100")
    assert start == datetime(2025, 10, 9, 8, 55, 0)
    assert MarketDiscovery._start_from_slug("no-number-here") is None


def test_market_discovery_uses_protocol_v2_position_ids():
    discovery = MarketDiscovery(Settings())
    start = datetime(2026, 11, 12, 12, 0, 0)
    market = discovery._build_market(
        {"slug": "btc-up-or-down"},
        {
            "conditionId": "0xcondition",
            "version": "v2",
            "outcomes": '["Up", "Down"]',
            "clobTokenIds": None,
            "positionIds": '["position-up", "position-down"]',
        },
        "series-1",
        start,
        start.replace(minute=5),
    )
    assert market is not None
    assert market.asset_version == 2
    assert market.up_token_id == "position-up"
    assert market.down_token_id == "position-down"


def _event_payload(start):
    slug_start = int(start.replace(tzinfo=timezone.utc).timestamp())
    return {
        "slug": f"btc-updown-5m-{slug_start}",
        "startDate": start.isoformat() + "Z",
        "endDate": (start + timedelta(minutes=5)).isoformat() + "Z",
        "markets": [{
            "conditionId": "0xcondition",
            "slug": f"btc-updown-5m-{slug_start}",
            "closed": False,
            "version": "v1",
            "outcomes": '["Up", "Down"]',
            "clobTokenIds": '["token-up", "token-down"]',
        }],
    }


def test_active_market_found_by_deterministic_slug_first():
    discovery = MarketDiscovery(Settings())
    start = datetime(2026, 11, 12, 12, 0, 0)
    calls = []

    async def fake_get(path, params=None):
        calls.append((path, params))
        return [_event_payload(start)]

    discovery._get = fake_get
    market = asyncio.run(discovery.get_active_market(start.replace(second=20, tzinfo=timezone.utc)))
    assert market is not None
    assert market.up_token_id == "token-up"
    slug_start = int(start.replace(tzinfo=timezone.utc).timestamp())
    assert calls == [("/events", {"slug": f"btc-updown-5m-{slug_start}"})]


def test_active_market_falls_back_to_series_slug_events():
    discovery = MarketDiscovery(Settings())
    start = datetime(2026, 11, 12, 12, 0, 0)
    calls = []

    async def fake_get(path, params=None):
        calls.append((path, params))
        if "slug" in (params or {}):
            return []  # slug lookup finds nothing
        return [_event_payload(start)]

    discovery._get = fake_get
    market = asyncio.run(discovery.get_active_market(start.replace(second=20, tzinfo=timezone.utc)))
    assert market is not None
    assert calls[-1][1]["series_slug"] == "btc-up-or-down-5m"
    assert calls[-1][1]["closed"] == "false"


def test_active_market_falls_back_to_series_detail_last():
    discovery = MarketDiscovery(Settings())
    start = datetime(2026, 11, 12, 12, 0, 0)
    calls = []

    async def fake_get(path, params=None):
        calls.append((path, params))
        if path == "/events":
            raise DiscoveryError("boom")
        if path == "/series":
            return [{"id": "series-1"}]
        return {"events": [_event_payload(start)]}

    discovery._get = fake_get
    market = asyncio.run(discovery.get_active_market(start.replace(second=20, tzinfo=timezone.utc)))
    assert market is not None
    assert calls[-2:] == [("/series", {"slug": "btc-up-or-down-5m"}), ("/series/series-1", None)]


def test_active_market_raises_only_when_every_lookup_fails():
    discovery = MarketDiscovery(Settings())

    async def fake_get(path, params=None):
        raise DiscoveryError("network down")

    discovery._get = fake_get
    import pytest

    with pytest.raises(DiscoveryError):
        asyncio.run(discovery.get_active_market(datetime(2026, 11, 12, 12, 0, 20, tzinfo=timezone.utc)))


class _FeeV2Client(_FakeV2Client):
    """Fake CLOB V2 client that also serves the market's fee parameters (``fd``)."""

    def __init__(self, response, fd):
        super().__init__(response)
        self.fd = fd
        self.lookups = 0

    def get_clob_market_info(self, condition_id):
        self.lookups += 1
        return {"t": [{"t": "tok"}], "fd": self.fd}

    def get_tick_size(self, _token_id):
        return "0.01"

    def get_neg_risk(self, _token_id):
        return False


def test_live_fill_records_taker_fee_from_market_parameters():
    engine = LiveExecutionEngine(Settings())
    client = _FeeV2Client(
        {
            "success": True, "orderID": "0xabc", "status": "matched",
            "makingAmount": "5", "takingAmount": "6.25",
        },
        {"r": 0.07, "e": 1},
    )
    engine._client = client
    asyncio.run(engine.prewarm(["tok"], "m"))
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
    engine._client = client
    asyncio.run(engine.prewarm(["tok"], "m"))
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


def test_data_api_positions_uses_v2_envelope_and_cursor(monkeypatch):
    import httpx

    settings = Settings(polymarket_funder_address="0x" + "1" * 40)
    engine = LiveExecutionEngine(settings)
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == "/v2/positions"
        assert request.url.params["user"] == settings.polymarket_funder_address
        assert request.url.params["filter_type"] == "TOKENS"
        assert request.url.params["filter_amount"] == "0.01"
        if len(seen) == 1:
            assert "cursor" not in request.url.params
            return httpx.Response(200, json={
                "data": [{"condition_id": "first", "current_size": 1}],
                "pagination": {"next_cursor": "page-2"},
            })
        assert request.url.params["cursor"] == "page-2"
        return httpx.Response(200, json={
            "data": [{"condition_id": "second", "current_size": 2}],
            "pagination": {"next_cursor": None},
        })

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_async_client(*args, transport=transport, **kwargs),
    )
    positions = asyncio.run(engine.fetch_onchain_positions())
    assert [item["condition_id"] for item in positions] == ["first", "second"]
    assert len(seen) == 2


def test_redeemable_condition_ids_reads_v2_position_fields():
    engine = LiveExecutionEngine(Settings())

    async def positions():
        return [
            {"condition_id": "0xabc", "redeemable": True, "current_size": "2"},
            {"condition_id": "0xdef", "redeemable": True, "current_size": "0"},
        ]

    engine.fetch_onchain_positions = positions
    assert asyncio.run(engine.redeemable_condition_ids()) == {"0xabc"}


@pytest.mark.parametrize("signature_type", [1, 2, 3])
def test_live_proxy_wallet_requires_funder_address(signature_type):
    engine = LiveExecutionEngine(
        Settings(
            live_trading_enabled=True,
            polymarket_geo_verified=True,
            polymarket_private_key="0x" + "1" * 64,
            polymarket_signature_type=signature_type,
            polymarket_funder_address="",
        )
    )
    with pytest.raises(LiveTradingUnavailable, match="POLYMARKET_FUNDER_ADDRESS"):
        engine._validate_gates()


def test_live_rejects_unknown_signature_type():
    engine = LiveExecutionEngine(
        Settings(
            live_trading_enabled=True,
            polymarket_geo_verified=True,
            polymarket_private_key="0x" + "1" * 64,
            polymarket_signature_type=7,
        )
    )
    with pytest.raises(LiveTradingUnavailable, match="SIGNATURE_TYPE"):
        engine._validate_gates()


def test_live_smart_contract_wallet_signature_type_is_accepted():
    engine = LiveExecutionEngine(
        Settings(
            live_trading_enabled=True,
            polymarket_geo_verified=True,
            polymarket_private_key="0x" + "1" * 64,
            polymarket_signature_type=3,
            polymarket_funder_address="0x" + "2" * 40,
        )
    )
    engine._validate_gates()


def test_live_validation_refreshes_cached_balance_before_reading_it():
    engine = LiveExecutionEngine(Settings())
    calls = []

    class FakeClient:
        def update_balance_allowance(self, params):
            calls.append("update")

        def get_balance_allowance(self, params):
            calls.append("get")
            return {"balance": "1210000", "allowance": "1000000000"}

    async def ready():
        return FakeClient()

    engine._get_client = ready
    result = asyncio.run(engine.validate())
    assert calls[0] == "update"
    assert result["balance"] == 1.21
