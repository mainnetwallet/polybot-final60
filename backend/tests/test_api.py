from __future__ import annotations

import asyncio
import time

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.models import AppState, Order
from app.database.session import session_scope
from app.main import create_app
from app.services.bot import BotError, TradingBot

from .helpers import make_market


def _enable_live(bot):
    previous = (
        bot.settings.live_trading_enabled,
        bot.settings.polymarket_geo_verified,
        bot.settings.polymarket_private_key,
    )
    bot.settings.live_trading_enabled = True
    bot.settings.polymarket_geo_verified = True
    bot.settings.polymarket_private_key = "0xdeadbeef"

    async def fake_validate():
        return {"balance": 10.0, "allowance": 1000.0}

    bot.live_engine.validate = fake_validate
    return previous


def _restore_live(bot, previous):
    (
        bot.settings.live_trading_enabled,
        bot.settings.polymarket_geo_verified,
        bot.settings.polymarket_private_key,
    ) = previous


def test_health_and_state():
    with TestClient(create_app()) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["db_revision"] == "0001_initial"
        state = client.get("/api/state").json()
        assert state["bot"]["mode"] in {"paper", "live"}
        assert state["strategy"]["name"] == "FINAL_60_SECONDS_MARKET_BUY"
        assert state["account"]["balance"] >= 0


def test_live_mode_requires_confirmation_and_updates_backend():
    with TestClient(create_app()) as client:
        bot = client.app.state.bot
        previous = _enable_live(bot)
        try:
            denied = client.post("/api/mode", json={"mode": "live"})
            assert denied.status_code == 400
            assert "confirmation" in denied.json()["detail"]

            wrong = client.post(
                "/api/mode",
                json={"mode": "live", "confirm_live": True, "confirmation_phrase": "yes"},
            )
            assert wrong.status_code == 400

            switched = client.post(
                "/api/mode",
                json={
                    "mode": "live",
                    "confirm_live": True,
                    "confirmation_phrase": "GO LIVE",
                },
            )
            assert switched.status_code == 200
            assert switched.json()["bot"]["mode"] == "live"
            assert client.get("/api/state").json()["account"]["mode"] == "live"

            with session_scope() as session:
                assert session.get(AppState, 1).mode == "live"

            back = client.post("/api/mode", json={"mode": "paper"})
            assert back.status_code == 200
            assert back.json()["bot"]["mode"] == "paper"
            with session_scope() as session:
                assert session.get(AppState, 1).mode == "paper"
        finally:
            _restore_live(bot, previous)


def test_live_start_requires_confirmation():
    with TestClient(create_app()) as client:
        bot = client.app.state.bot
        previous = _enable_live(bot)
        try:
            assert client.post(
                "/api/mode",
                json={"mode": "live", "confirm_live": True, "confirmation_phrase": "GO LIVE"},
            ).status_code == 200

            denied = client.post("/api/bot/start", json={})
            assert denied.status_code == 400

            started = client.post(
                "/api/bot/start",
                json={"confirm_live": True, "confirmation_phrase": "GO LIVE"},
            )
            assert started.status_code == 200
            assert started.json()["bot"]["status"] == "RUNNING"
            assert started.json()["bot"]["mode"] == "live"

            stopped = client.post("/api/bot/stop")
            assert stopped.json()["bot"]["status"] == "STOPPED"
        finally:
            client.post("/api/mode", json={"mode": "paper"})
            _restore_live(bot, previous)


async def test_stop_during_live_start_cannot_be_overridden_by_late_validation():
    entered_validation = asyncio.Event()
    release_validation = asyncio.Event()

    class FakeLiveEngine:
        allowed = False

        def block_new_orders(self):
            self.allowed = False

        def allow_new_orders(self):
            self.allowed = True

        async def prewarm(self, *_args):
            return None

    class FakeOrders:
        def reset_trigger_cache(self):
            return None

        async def wait_for_submissions(self):
            return None

        async def cancel_live_orders(self):
            return 0

    class FakeLogs:
        def log(self, *_args, **_kwargs):
            return None

    bot = TradingBot.__new__(TradingBot)
    bot._lock = asyncio.Lock()
    bot._stop_generation = 0
    bot._status = "STOPPED"
    bot._mode = "live"
    bot._kill_switch = False
    bot._started_at = None
    bot._market = None
    bot._risk_halt_reason = None
    bot._risk_halt_logged = False
    bot.live_engine = FakeLiveEngine()
    bot.orders = FakeOrders()
    bot.logs = FakeLogs()

    async def live_gate(*_args, **_kwargs):
        entered_validation.set()
        await release_validation.wait()
        return {}

    async def no_reconciliation():
        return None

    async def no_broadcast():
        return None

    async def snapshot():
        return {"bot": {"status": bot._status}}

    bot._live_gate = live_gate
    bot._reconcile_open_live_orders = no_reconciliation
    bot._broadcast_state = no_broadcast
    bot.snapshot = snapshot

    starting = asyncio.create_task(bot.start_bot(True, "GO LIVE"))
    await entered_validation.wait()
    stopping = asyncio.create_task(bot.stop_bot())
    await asyncio.sleep(0)
    release_validation.set()
    try:
        await starting
    except BotError as exc:
        assert "canceled" in str(exc)
    else:
        raise AssertionError("a pending stop must cancel a live start")
    await stopping

    assert bot._status == "STOPPED"
    assert bot.live_engine.allowed is False


def test_mode_switch_blocked_while_running():
    with TestClient(create_app()) as client:
        assert client.post("/api/bot/start", json={}).status_code == 200
        blocked = client.post(
            "/api/mode",
            json={"mode": "paper"},
        )
        assert blocked.status_code == 400
        assert "stop the bot" in blocked.json()["detail"].lower()
        client.post("/api/bot/stop")


def test_settings_patch_and_validation():
    with TestClient(create_app()) as client:
        ok = client.patch("/api/settings", json={"trigger_threshold": 0.82})
        assert ok.status_code == 200
        assert ok.json()["trigger_threshold"] == 0.82

        bad = client.patch("/api/settings", json={"max_execution_price": 0.5})
        assert bad.status_code == 400

        bad_window = client.patch("/api/settings", json={"final_window_seconds": 0})
        assert bad_window.status_code in {400, 422}


def test_settings_never_expose_secrets():
    with TestClient(create_app()) as client:
        bot = client.app.state.bot
        previous = _enable_live(bot)
        try:
            body = client.get("/api/settings").text
            assert "0xdeadbeef" not in body
            assert "POLYMARKET_PRIVATE_KEY" not in body
            assert "private" not in body.lower()
        finally:
            _restore_live(bot, previous)


def test_unresolved_order_from_previous_market_does_not_block_new_market():
    with TestClient(create_app()) as client:
        bot = client.app.state.bot
        bot._mode = "live"
        with session_scope() as session:
            session.add(
                Order(
                    order_key="live:pending-exposure",
                    client_order_id="pending-exposure",
                    mode="live",
                    market_id="old-market",
                    side="UP",
                    token_id="position-up",
                    status="SUBMITTED",
                    requested_notional=5.0,
                    requested_price=0.8,
                )
            )

        market = make_market()
        context = bot._risk_context(market, bot.runtime.get(), int(time.time() * 1000))
        assert context.open_positions == 0

        with session_scope() as session:
            session.add(
                Order(
                    order_key="live:current-exposure",
                    client_order_id="current-exposure",
                    mode="live",
                    market_id=market.market_id,
                    side="UP",
                    token_id="position-up",
                    status="SUBMITTED",
                    requested_notional=5.0,
                    requested_price=0.8,
                )
            )
        context = bot._risk_context(market, bot.runtime.get(), int(time.time() * 1000))
        assert context.open_positions == 1


def test_paper_reset_requires_confirmation():
    with TestClient(create_app()) as client:
        denied = client.post("/api/paper/reset", json={"balance": 10.0})
        assert denied.status_code == 400
        ok = client.post("/api/paper/reset", json={"balance": 10.0, "confirm": True})
        assert ok.status_code == 200
        assert ok.json()["account"]["balance"] == 10.0


def test_database_survives_restart():
    with TestClient(create_app()) as client:
        saved = client.patch("/api/settings", json={"trigger_threshold": 0.83})
        assert saved.status_code == 200
        with session_scope() as session:
            session.add(
                Order(
                    order_key="paper:persisted-market",
                    client_order_id="persist-1",
                    mode="paper",
                    market_id="persisted-market",
                    side="UP",
                    token_id="token",
                    status="FILLED",
                    requested_notional=5.0,
                    requested_price=0.85,
                    filled_size=5.88,
                    avg_fill_price=0.85,
                )
            )

    restarted = create_app()
    with TestClient(restarted) as client:
        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert settings.json()["trigger_threshold"] == 0.83

        orders = client.get("/api/orders", params={"mode": "paper"}).json()
        assert any(o["market_id"] == "persisted-market" for o in orders)


def test_analytics_are_separated_by_mode():
    with TestClient(create_app()) as client:
        with session_scope() as session:
            session.add(
                Order(
                    order_key="live:analytics-market",
                    client_order_id="live-1",
                    mode="live",
                    market_id="analytics-market",
                    side="UP",
                    token_id="token",
                    status="FILLED",
                    requested_notional=5.0,
                    requested_price=0.9,
                    filled_size=5.5,
                    avg_fill_price=0.9,
                )
            )
        paper = client.get("/api/analytics", params={"mode": "paper"}).json()
        live = client.get("/api/analytics", params={"mode": "live"}).json()
        assert paper["mode"] == "paper"
        assert live["mode"] == "live"
        assert live["total_trades"] >= 1
        assert paper["total_trades"] == 0


def test_logs_endpoint_returns_persisted_events():
    with TestClient(create_app()) as client:
        assert client.post("/api/bot/start", json={}).status_code == 200
        assert client.post("/api/bot/stop").status_code == 200
    with TestClient(create_app()) as client:
        logs = client.get("/api/logs", params={"limit": 50}).json()
        assert isinstance(logs, list)
        assert any(entry["category"] in {"system", "market"} for entry in logs)


def test_daily_reset_requires_confirmation():
    with TestClient(create_app()) as client:
        assert client.post("/api/daily/reset", json={}).status_code == 400
        ok = client.post("/api/daily/reset", json={"confirm": True})
        assert ok.status_code == 200


def test_server_reset_requires_phrase_and_wipes_data():
    with TestClient(create_app()) as client:
        assert client.post("/api/server/reset", json={"confirm": True}).status_code == 400
        assert (
            client.post(
                "/api/server/reset", json={"confirm": True, "confirmation_phrase": "nope"}
            ).status_code
            == 400
        )
        client.patch("/api/settings", json={"trigger_threshold": 0.83})
        ok = client.post(
            "/api/server/reset",
            json={"confirm": True, "confirmation_phrase": "RESET ALL"},
        )
        assert ok.status_code == 200
        assert ok.json()["bot"]["status"] == "STOPPED"
        assert ok.json()["bot"]["mode"] == "paper"
        # setting overrides are gone -> back to .env default
        assert client.get("/api/settings").json()["trigger_threshold"] == 0.80


def test_api_token_is_optional_and_protects_everything_but_health():
    app = create_app()
    app.state.settings.api_token = "s3cret-token"
    try:
        with TestClient(app) as client:
            # monitors keep working
            assert client.get("/api/health").status_code == 200
            # no / wrong token
            assert client.get("/api/state").status_code == 401
            assert client.post("/api/bot/stop").status_code == 401
            assert client.post("/api/daily/reset", json={"confirm": True}).status_code == 401
            assert client.get("/api/state", headers={"X-API-Key": "nope"}).status_code == 401
            # right token, either header style
            assert client.get("/api/state", headers={"X-API-Key": "s3cret-token"}).status_code == 200
            assert client.get(
                "/api/state", headers={"Authorization": "Bearer s3cret-token"}
            ).status_code == 200
    finally:
        app.state.settings.api_token = ""


def test_api_is_open_without_a_token():
    with TestClient(create_app()) as client:
        assert client.get("/api/state").status_code == 200


def test_websocket_requires_token_when_configured():
    from starlette.websockets import WebSocketDisconnect

    from app.config.settings import get_settings

    settings = get_settings()
    settings.api_token = "s3cret-token"  # the ws router reads the token when the app is built
    try:
        with TestClient(create_app()) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/api/ws"):
                    pass
            with client.websocket_connect("/api/ws?token=s3cret-token") as ws:
                assert ws.receive_json()["type"] == "state"
    finally:
        settings.api_token = ""


def test_min_price_gap_setting_defaults_to_20_and_is_patchable():
    with TestClient(create_app()) as client:
        assert client.get("/api/settings").json()["min_price_gap_usd"] == 20.0
        assert client.get("/api/settings").json()["stage2_min_price_gap_usd"] == 25.0
        assert client.get("/api/settings").json()["stage3_min_price_gap_usd"] == 30.0
        updated = client.patch("/api/settings", json={"min_price_gap_usd": 35})
        assert updated.status_code == 200
        assert updated.json()["min_price_gap_usd"] == 35.0
        assert client.get("/api/state").json()["config"]["min_price_gap_usd"] == 35.0
        assert client.patch("/api/settings", json={"min_price_gap_usd": -1}).status_code == 422
        # 0 switches the rule off
        assert client.patch("/api/settings", json={"min_price_gap_usd": 0}).json()["min_price_gap_usd"] == 0.0


def test_gap_inputs_are_read_on_the_event_loop_not_in_the_db_thread():
    import asyncio
    import threading

    from app.strategy.types import Signal, StrategyEvaluation

    from .helpers import make_market

    with TestClient(create_app()) as client:
        bot = client.app.state.bot
        market = make_market(remaining=40)
        seen: list[str] = []
        real = bot.btc.gap_inputs

        def spy(m):
            seen.append(threading.current_thread().name)
            return real(m)

        bot.btc.gap_inputs = spy
        signal = Signal(
            market_id=market.market_id, side="UP", token_id=market.up_token_id, best_ask=0.9,
            ask_size=10.0, remaining_seconds=40.0, threshold=0.8, observed_at_ms=int(time.time() * 1000),
            book_age_ms=10.0, stage=1,
        )
        evaluation = StrategyEvaluation(True, 40.0, 0.9, 0.1, True, False, [signal], "trigger")

        async def run():
            await bot._handle_trading(market, evaluation, bot.runtime.get(), int(time.time() * 1000))
            return threading.current_thread().name

        loop_thread = client.portal.call(run)
        assert seen and set(seen) == {loop_thread}  # never from an asyncio.to_thread worker

        # a precomputed value is used as is
        context = bot._risk_context(market, bot.runtime.get(), int(time.time() * 1000), (101.0, 100.0, ""))
        assert (context.btc_price, context.price_to_beat) == (101.0, 100.0)
