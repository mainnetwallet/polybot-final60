"""Compare every possible Price To Beat source for the CURRENT 5m window against the Polymarket page.

Run from the repo root in the MIDDLE of a window (e.g. 1-2 minutes after it opened):
    .venv\\Scripts\\python.exe scripts\\check_price_to_beat.py
Then compare the printed values with the "Price To Beat" shown on the Polymarket event page.
Prints: Gamma priceToBeat, the website crypto-price openPrice (all param variants), and whether the
RTDS subscribe batch already contains history reaching back to the window start (and the tick there).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.config.settings import Settings  # noqa: E402
from app.market_data.btc_price import BROWSER_HEADERS, BtcPriceFeed  # noqa: E402


def iso(t: int, ms: bool = False) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + (".000Z" if ms else "Z")


async def main() -> None:
    settings = Settings()
    now = int(time.time())
    start = now - now % 300
    slug = f"{settings.event_slug_prefix}-{start}"
    print(f"window start {iso(start)}  (now +{now - start}s)  slug {slug}\n")
    async with httpx.AsyncClient(timeout=8, headers=BROWSER_HEADERS, follow_redirects=True) as http:
        try:
            r = await http.get(f"{settings.gamma_base_url.rstrip('/')}/events", params={"slug": slug})
            data = r.json()
            event = data[0] if isinstance(data, list) and data else {}
            print("GAMMA eventMetadata:", json.dumps(event.get("eventMetadata")))
            for m in event.get("markets") or []:
                print("GAMMA market eventMetadata:", json.dumps(m.get("eventMetadata")))
        except Exception as exc:
            print("GAMMA error:", exc)
        print()
        for ms in (False, True):
            try:
                r = await http.get(
                    settings.crypto_price_api_url,
                    params={"symbol": "BTC", "eventStartTime": iso(start, ms), "variant": "fiveminute", "endDate": iso(start + 300, ms)},
                )
                print(f"SITE crypto-price (ms={ms}) HTTP {r.status_code}: {r.text[:300]}")
            except Exception as exc:
                print(f"SITE error (ms={ms}):", exc)
    print()
    feed = BtcPriceFeed(settings)
    try:
        async with feed._connect(0) as ws:
            await ws.send(feed._subscribe_frame(0))
            end = time.monotonic() + 8
            while time.monotonic() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except (TimeoutError, asyncio.TimeoutError):
                    await ws.send("PING")
                    continue
                feed.handle_message(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception as exc:
        print("RTDS error:", exc)
    ticks = sorted(feed._ticks)
    print(f"RTDS ticks received: {len(ticks)}")
    if ticks:
        print(f"  oldest {iso(ticks[0][0] // 1000)}  newest {iso(ticks[-1][0] // 1000)}")
        at = [t for t in ticks if start * 1000 <= t[0] <= start * 1000 + 1000]
        print("  tick at window start:", at[:1] or "NOT in the history batch")
