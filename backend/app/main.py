from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import AuthService, build_auth_router
from app.api.routes import build_router
from app.api.ws import RealtimeHub, build_ws_router
from app.config.runtime import SettingsService
from app.config.settings import LOG_DIR, get_settings
from app.database.migrate import ensure_schema
from app.portfolio.analytics import AnalyticsService
from app.portfolio.service import Portfolio
from app.services.bot import TradingBot


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    if any(getattr(h, "_polybot", False) for h in root.handlers):
        return
    root.setLevel(settings.log_level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream._polybot = True  # type: ignore[attr-defined]
    root.addHandler(stream)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "backend.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler._polybot = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    settings = get_settings()
    hub = RealtimeHub()
    runtime = SettingsService(settings)
    portfolio = Portfolio(settings)
    analytics = AnalyticsService()
    bot = TradingBot(settings, runtime, portfolio, analytics, hub)
    auth = AuthService(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        configure_logging()
        if settings.api_exposed_without_token:
            logging.getLogger(__name__).warning(
                "API is bound to %s without API_TOKEN: anyone who can reach it can start/stop the bot. "
                "Set API_TOKEN (or bind HOST=127.0.0.1).",
                settings.host,
            )
        if not auth.enabled:
            logging.getLogger(__name__).warning(
                "Dashboard login is OFF (ADMIN_PASSWORD is empty). Set ADMIN_PASSWORD in .env to require a login."
            )
        ensure_schema()
        await bot.initialize()
        try:
            yield
        finally:
            await bot.shutdown()

    app = FastAPI(
        title="Polymarket Final-60s BTC Bot",
        version="1.0.0",
        description="Final 80 seconds, marketable BUY when the ask is >= 0.80.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Minimal public liveness probe for Render / UptimeRobot / cron-job.org pings. It never touches
    # the bot, DB or auth and leaks nothing (use /api/health for the detailed status).
    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    app.include_router(build_auth_router(auth))
    app.include_router(build_router(bot, runtime, settings, analytics, auth))
    app.include_router(build_ws_router(hub, bot.snapshot, settings.api_token, auth))
    # Serve the prebuilt dashboard (backend/app/static, built from frontend/) from the same origin as the
    # API, so cookies and the WebSocket work without a separate proxy. Registered last: API routes win.
    static_dir = Path(__file__).resolve().parent / "static"
    index_file = static_dir / "index.html"
    if index_file.is_file():
        static_root = static_dir.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def dashboard(full_path: str) -> FileResponse:
            if full_path.startswith("api/") or full_path == "api":
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (static_dir / full_path).resolve()
            if full_path and candidate.is_file() and static_root in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index_file)

    app.state.bot = bot
    app.state.settings = settings
    app.state.auth = auth
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host=get_settings().host, port=get_settings().port, reload=False)
