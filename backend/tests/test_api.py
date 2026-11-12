from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.models import AppState, Order
from app.database.session import session_scope
from app.main import create_app


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
