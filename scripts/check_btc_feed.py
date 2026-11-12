"""Live diagnostic for the Price To Beat / Current Price feed.

Run from the repo root:   .venv\Scripts\python.exe scripts\check_btc_feed.py
Prints exactly what Polymarket answers (HTTP status/body for the website endpoint, and the first
frames of every RTDS connection variant) so a blocked/changed endpoint is obvious.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.config.settings import Settings  # noqa: E402
from app.market_data.btc_price import BROWSER_HEADERS, BtcPriceFeed  # noqa: E402


async def check_price_to_beat(settings: Settings) -> None:
    now = datetime.now(timezone.utc)
    start = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
    end = start + timedelta(minutes=5)
    print(f"== price to beat: window {start:%H:%M:%S}Z -> {end:%H:%M:%S}Z")
    async with httpx.AsyncClient(timeout=8, headers=BROWSER_HEADERS, follow_redirects=True) as http:
        for suffix in ("Z", ".000Z"):
            params = {
                "symbol": "BTC",
                "eventStartTime": start.strftime("%Y-%m-%dT%H:%M:%S") + suffix,
                "variant": "fiveminute",
                "endDate": end.strftime("%Y-%m-%dT%H:%M:%S") + suffix,
            }
            try:
                r = await http.get(settings.crypto_price_api_url, params=params)
                print(f"  [{suffix}] HTTP {r.status_code}: {r.text[:300]}")
            except Exception as exc:
                print(f"  [{suffix}] ERROR {type(exc).__name__}: {exc}")


async def check_ws(settings: Settings, mode: int, seconds: float = 10.0) -> None:
    feed = BtcPriceFeed(settings)
    print(f"== RTDS mode {mode} (filtered={mode >= 2}, origin={mode % 2 == 1})")
    try:
        async with feed._connect(mode) as ws:
            await ws.send(feed._subscribe_frame(mode))
            end = time.monotonic() + seconds
            shown = 0
            while time.monotonic() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except (TimeoutError, asyncio.TimeoutError):
                    await ws.send("PING")
                    continue
                text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                feed.handle_message(text)
                if shown < 3 and text.strip().upper() != "PONG":
                    print("  frame:", text[:220])
                    shown += 1
            print(f"  ticks parsed: {feed._messages}  latest: {feed.latest}")
    except Exception as exc:
        print(f"  ERROR {type(exc).__name__}: {exc}")


async def main() -> None:
    settings = Settings()
    await check_price_to_beat(settings)
    for mode in range(4):
        await check_ws(settings, mode)


if __name__ == "__main__":
    asyncio.run(main())
