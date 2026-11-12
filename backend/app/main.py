from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        configure_logging()
        ensure_schema()
        await bot.initialize()
        try:
            yield
        finally:
            await bot.shutdown()

    app = FastAPI(
        title="Polymarket Final-60s BTC Bot",
        version="1.0.0",
        description="Final 60 seconds, marketable BUY when the ask is >= 0.80.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_router(bot, runtime, settings, analytics))
    app.include_router(build_ws_router(hub, bot.snapshot))
    app.state.bot = bot
    app.state.settings = settings
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host=get_settings().host, port=get_settings().port, reload=False)
