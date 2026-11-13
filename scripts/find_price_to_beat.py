"""Find which Polymarket endpoint carries the official Price To Beat of the CURRENT 5m window.

Usage (mid-window, with the number the Polymarket page shows):
    .venv\\Scripts\\python.exe scripts\\find_price_to_beat.py 69233.33
Fetches several Polymarket URLs and reports every place where that number (+-0.02) or the key
"priceToBeat" appears.
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.config.settings import Settings  # noqa: E402
from app.market_data.btc_price import BROWSER_HEADERS  # noqa: E402

NUM = re.compile(r"-?\d{4,6}\.\d{1,12}")


async def main(target: float) -> None:
    s = Settings()
    now = int(time.time())
    start = now - now % 300
    slug = f"{s.event_slug_prefix}-{start}"
    gamma = s.gamma_base_url.rstrip("/")
    urls = [
        f"{gamma}/events?slug={slug}",
        f"{gamma}/events/slug/{slug}",
        f"{gamma}/markets?slug={slug}",
        f"https://polymarket.com/event/{slug}",
        f"https://polymarket.com/api/event/slug?slug={slug}",
        f"https://polymarket.com/api/crypto/crypto-price?symbol=BTC&eventStartTime={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start))}&variant=fiveminute&endDate={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start + 300))}",
    ]
    print(f"slug {slug}  now +{now - start}s  target {target}\n")
    async with httpx.AsyncClient(timeout=15, headers=BROWSER_HEADERS, follow_redirects=True) as http:
        for url in urls:
            try:
                r = await http.get(url)
            except Exception as exc:
                print(f"[ERR] {url}\n      {type(exc).__name__}: {exc}\n")
                continue
            text = r.text
            print(f"[{r.status_code}] {url}  ({len(text)} bytes)")
            for m in re.finditer(r"priceToBeat", text):
                print("   key priceToBeat:", text[max(0, m.start() - 40): m.end() + 80].replace("\n", " "))
            hits = [m for m in NUM.finditer(text) if abs(float(m.group()) - target) <= 0.02]
            for m in hits[:5]:
                print("   MATCH:", text[max(0, m.start() - 80): m.end() + 40].replace("\n", " "))
            if not hits and "priceToBeat" not in text:
                print("   (no match)")
            print()


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1])))
