"""Live diagnostic for the Price To Beat / Current Price feed.

Run from the repo root:   .venv\Scripts\python.exe scripts\check_btc_feed.py
Prints exactly what Polymarket answers so a blocked/changed endpoint is obvious:
  * Gamma eventMetadata.priceToBeat (the default Price To Beat source) for the current window
  * the first frames of every RTDS connection variant on the configured topic (default: Chainlink 60s TWAP)
Compare the printed values with the Polymarket event page of the same window.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
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


async def check_site(settings: Settings) -> None:
    now = int(time.time())
    start = now - now % 300
    from datetime import datetime, timezone

    f = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"  # noqa: E731
    print(f"== website open-price endpoint: {settings.crypto_price_api_url}")
    async with httpx.AsyncClient(timeout=8, headers=BROWSER_HEADERS, follow_redirects=True) as http:
        try:
            r = await http.get(
                settings.crypto_price_api_url,
                params={"symbol": "BTC", "eventStartTime": f(start), "variant": "fiveminute", "endDate": f(start + 300)},
            )
            print(f"  HTTP {r.status_code}  body: {r.text[:300]}")
        except Exception as exc:
            print(f"  ERROR {type(exc).__name__}: {exc}")


async def check_ws(settings: Settings, mode: int, seconds: float = 10.0) -> None:
    feed = BtcPriceFeed(settings)
    print(f"== RTDS topic {"crypto_prices_twap_sixty"} mode {mode} (filtered={mode >= 2}, origin={mode % 2 == 1})")
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
    await check_gamma(settings)
    await check_site(settings)
    for mode in range(4):
        await check_ws(settings, mode)


if __name__ == "__main__":
    asyncio.run(main())
