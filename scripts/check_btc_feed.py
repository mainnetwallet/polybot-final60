"""Live diagnostic for the Price To Beat / Current Price feed.

Run from the repo root:   .venv\Scripts\python.exe scripts\check_btc_feed.py
Prints exactly what Polymarket answers so a blocked/changed endpoint is obvious:
  * Gamma eventMetadata.priceToBeat (the default Price To Beat source) for the current window
  * the legacy website crypto-price endpoint, for comparison
  * the first frames of every RTDS connection variant on the configured topic (default: Chainlink 60s TWAP)
Compare the printed values with the Polymarket event page of the same window.
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


async def check_gamma(settings: Settings) -> None:
    now = int(time.time())
    start = now - now % 300
    slug = f"{settings.event_slug_prefix}-{start}"
    print(f"== gamma priceToBeat: {slug}")
    async with httpx.AsyncClient(timeout=8, headers=BROWSER_HEADERS, follow_redirects=True) as http:
        try:
            r = await http.get(f"{settings.gamma_base_url.rstrip('/')}/events", params={"slug": slug})
            data = r.json()
            event = data[0] if isinstance(data, list) and data else {}
            print(f"  HTTP {r.status_code}  eventMetadata: {json.dumps(event.get('eventMetadata'))}")
        except Exception as exc:
            print(f"  ERROR {type(exc).__name__}: {exc}")


async def check_price_to_beat(settings: Settings) -> None:
    now = datetime.now(timezone.utc)
    start = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
    end = start + timedelta(minutes=5)
    print(f"== legacy crypto-price endpoint: window {start:%H:%M:%S}Z -> {end:%H:%M:%S}Z")
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
    print(f"== RTDS topic {settings.rtds_price_topic} mode {mode} (filtered={mode >= 2}, origin={mode % 2 == 1})")
    try:
        async with feed._connect(mode) as ws:
            await ws.send(feed._subscribe_frame(mode, settings.rtds_price_topic))
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
    await check_gamma(settings)
    await check_price_to_beat(settings)
    for mode in range(4):
        await check_ws(settings, mode)


if __name__ == "__main__":
    asyncio.run(main())
