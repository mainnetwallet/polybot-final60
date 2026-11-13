from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import select

from app.api.schemas import (
    KillSwitchRequest,
    ModeRequest,
    ResetDailyRequest,
    ResetPaperRequest,
    ResetServerRequest,
    SettingsPatch,
    StartRequest,
)
from app.config.runtime import SettingsService
from app.config.settings import Settings
from app.database.models import LogEntry, utcnow
from app.database.migrate import current_revision
from app.database.session import session_scope
from app.services.bot import RESET_SERVER_PHRASE, BotError, LiveGateError, ModeSwitchError, TradingBot, iso_z


def build_router(
    bot: TradingBot,
    runtime: SettingsService,
    settings: Settings,
    analytics,
) -> APIRouter:
    def require_token(request: Request) -> None:
        """Opt-in shared-secret auth (API_TOKEN). No token configured = open, as before."""
        expected = settings.api_token.strip()
        if not expected:
            return
        supplied = request.headers.get("x-api-key", "")
        authorization = request.headers.get("authorization", "")
        if not supplied and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing API token")

    # /health stays open so uptime monitors keep working; everything else needs the token.
    public = APIRouter(prefix="/api")
    router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    def _mode(value: str | None) -> str:
        if value is None:
            return bot.mode
        if value not in {"paper", "live"}:
            raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")
        return value

    @public.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "time": iso_z(utcnow()),
            "bot_status": bot.status,
            "mode": bot.mode,
            "market_data": bot.data.status,
            "db_revision": current_revision(),
        }

    @router.get("/state")
    async def state() -> dict:
        return await bot.snapshot()

    @router.post("/bot/start")
    async def start(request: StartRequest) -> dict:
        try:
            return await bot.start_bot(request.confirm_live, request.confirmation_phrase)
        except (LiveGateError, BotError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/bot/stop")
    async def stop() -> dict:
        return await bot.stop_bot("operator")

    @router.post("/mode")
    async def switch_mode(request: ModeRequest) -> dict:
        try:
            return await bot.switch_mode(
                request.mode, request.confirm_live, request.confirmation_phrase
            )
        except (ModeSwitchError, LiveGateError, BotError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/kill-switch")
    async def kill_switch(request: KillSwitchRequest) -> dict:
        try:
            return await bot.set_kill_switch(request.active)
        except BotError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/settings")
    async def get_settings_route() -> dict:
        return runtime.get().to_public_dict()

    @router.patch("/settings")
    async def patch_settings(request: SettingsPatch) -> dict:
        changes = request.changes()
        if not changes:
            raise HTTPException(status_code=400, detail="no supported settings supplied")
        try:
            return await bot.update_settings(changes)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/settings/reset")
    async def reset_settings_route() -> dict:
        return await bot.reset_settings()

    @router.get("/orders")
    async def orders(
        mode: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict]:
        return await bot.recent_orders(_mode(mode), limit)

    @router.get("/positions")
    async def positions(
        mode: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict]:
        return await bot.recent_positions(_mode(mode), limit)

    @router.get("/triggers")
    async def triggers(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
        return await bot.recent_triggers(limit)

    @router.get("/analytics")
    async def analytics_route(mode: str | None = Query(default=None)) -> dict:
        return await bot.analytics_summary(_mode(mode))

    @router.get("/logs")
    async def logs(
        limit: int = Query(default=200, ge=1, le=1000),
        category: str | None = Query(default=None),
        level: Literal["INFO", "WARNING", "ERROR", "CRITICAL"] | None = Query(default=None),
    ) -> list[dict]:
        with session_scope() as session:
            query = select(LogEntry)
            if category:
                query = query.where(LogEntry.category == category)
            if level:
                query = query.where(LogEntry.level == level)
            query = query.order_by(LogEntry.id.desc()).limit(limit)
            rows = session.execute(query).scalars().all()
            return [
                {
                    "id": row.id,
                    "level": row.level,
                    "category": row.category,
                    "message": row.message,
                    "payload": row.payload,
                    "mode": row.mode,
                    "created_at": iso_z(row.created_at),
                }
                for row in rows
            ]

    @router.get("/live/readiness")
    async def live_readiness() -> dict:
        return bot.live_readiness()

    @router.post("/live/validate")
    async def live_validate() -> dict:
        try:
            result = await bot.live_engine.validate()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @router.post("/paper/reset")
    async def reset_paper(request: ResetPaperRequest) -> dict:
        if not request.confirm:
            raise HTTPException(status_code=400, detail="confirm must be true")
        try:
            return await bot.reset_paper_account(request.balance)
        except BotError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/daily/reset")
    async def reset_daily(request: ResetDailyRequest) -> dict:
        if not request.confirm:
            raise HTTPException(status_code=400, detail="confirm must be true")
        try:
            return await bot.reset_daily_stats()
        except BotError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/server/reset")
    async def reset_server(request: ResetServerRequest) -> dict:
        if not request.confirm:
            raise HTTPException(status_code=400, detail="confirm must be true")
        if request.confirmation_phrase.strip() != RESET_SERVER_PHRASE:
            raise HTTPException(
                status_code=400,
                detail=f"type {RESET_SERVER_PHRASE} to confirm the full reset",
            )
        try:
            return await bot.reset_server()
        except BotError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/config")
    async def config() -> dict:
        return {
            "series_slug": settings.series_slug,
            "clob_ws_url": settings.clob_ws_url,
            "strategy": "FINAL_60_SECONDS_MARKET_BUY",
            "trigger_threshold": runtime.get().trigger_threshold,
            "final_window_seconds": runtime.get().final_window_seconds,
        }

    root = APIRouter()
    root.include_router(public)
    root.include_router(router)
    return root
