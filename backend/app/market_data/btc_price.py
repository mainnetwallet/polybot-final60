"""BTC/USD reference price feed and the per-window "price to beat".

Polymarket's 5-minute BTC markets resolve UP when the Chainlink BTC/USD price at the
window end is >= the price at the window start (the "price to beat"). Only official
Polymarket data is used here, with no estimates:
  * current price  -> Chainlink BTC/USD stream from Polymarket's real-time data socket (RTDS)
  * price to beat  -> Polymarket's own value (Gamma event metadata, or the crypto-price
                      endpoint the website uses)
If a value is not available yet it is shown as missing rather than guessed.

It is display-only: nothing here feeds the strategy or order path, and every failure
degrades to "no value" instead of raising.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import websockets

from app.config.settings import Settings
from app.market_discovery.types import MarketInfo

logger = logging.getLogger(__name__)

STALE_AFTER_MS = 10_000
OPEN_LOOKUP_RETRY_SECONDS = 4.0
OPEN_LOOKUP_MAX_ATTEMPTS = 30  # ~2 min of retries: the official value can appear a bit after the window opens


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class BtcPriceFeed:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._latest: tuple[int, float] | None = None  # (timestamp_ms, price) from the RTDS stream
        self._connected = False
        self._last_error: str | None = None
        self._reconnects = 0
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._market: MarketInfo | None = None
        self._market_changed = asyncio.Event()
        # market_id -> (price, source) with source "gamma" or "polymarket"
        self._opens: dict[str, tuple[float, str]] = {}
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ public
    @property
    def latest(self) -> tuple[int, float] | None:
        return self._latest

    def set_market(self, market: MarketInfo | None) -> None:
        self._market = market
        self._market_changed.set()

    async def start(self) -> None:
        if self._running or not self._settings.btc_price_enabled:
            return
        self._running = True
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            headers={"User-Agent": self._settings.http_user_agent, "Accept": "application/json"},
        )
        self._tasks = [
            asyncio.create_task(self._stream(), name="btc-price-ws"),
            asyncio.create_task(self._open_price_loop(), name="btc-open-price"),
        ]

    async def stop(self) -> None:
        self._running = False
        self._market_changed.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        self._connected = False
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def price_to_beat(self, market: MarketInfo | None) -> tuple[float, str] | None:
        """Polymarket's official price to beat for this window, or None until it is published."""
        if market is None:
            return None
        return self._opens.get(market.market_id)

    def snapshot(self, market: MarketInfo | None) -> dict:
        latest = self.latest
        now = _now_ms()
        price = latest[1] if latest else None
        age_ms = now - latest[0] if latest else None
        beat = self.price_to_beat(market)
        beat_price = beat[0] if beat else None
        diff = price - beat_price if price is not None and beat_price is not None else None
        return {
            "price": price,
            "price_to_beat": beat_price,
            "price_to_beat_source": beat[1] if beat else None,
            "diff": diff,
            "age_ms": age_ms,
            "stale": age_ms is None or age_ms > STALE_AFTER_MS,
            "connected": self._connected,
            "enabled": self._settings.btc_price_enabled,
            "last_error": self._last_error,
        }

    # ----------------------------------------------------------------- internals
    def _trim_opens(self) -> None:
        while len(self._opens) > 24:
            self._opens.pop(next(iter(self._opens)))

    def _record(self, ts_ms: int, price: float) -> None:
        if self._latest is not None and ts_ms < self._latest[0]:
            return  # out-of-order tick
        self._latest = (ts_ms, price)

    def handle_message(self, raw: str) -> None:
        """Parse an RTDS frame. Accepts single updates and the initial batch."""
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return
        topic = str(message.get("topic") or "")
        if topic and "chainlink" not in topic:
            return
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").lower()
        if symbol and not symbol.startswith("btc"):
            return
        rows = payload.get("data")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    self._ingest(row)
        else:
            self._ingest(payload)

    def _ingest(self, row: dict) -> None:
        price = _to_float(row.get("value"))
        if price is None:
            return
        try:
            ts_ms = int(row.get("timestamp"))
        except (TypeError, ValueError):
            ts_ms = _now_ms()
        self._record(ts_ms, price)

    async def _stream(self) -> None:
        backoff = 0.5
        subscribe = json.dumps(
            {
                "action": "subscribe",
                "subscriptions": [
                    {
                        "topic": "crypto_prices_chainlink",
                        "type": "*",
                        "filters": json.dumps({"symbol": "btc/usd"}),
                    }
                ],
            }
        )
        while self._running:
            try:
                async with websockets.connect(
                    self._settings.rtds_ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    await ws.send(subscribe)
                    self._connected = True
                    self._last_error = None
                    next_ping = time.monotonic() + 5.0
                    while self._running:
                        if time.monotonic() >= next_ping:
                            await ws.send("PING")
                            next_ping = time.monotonic() + 5.0
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except TimeoutError:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        if not raw.strip() or raw.strip().upper() == "PONG":
                            continue
                        backoff = 0.5
                        self.handle_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._reconnects += 1
                logger.warning("btc price ws error: %s", self._last_error)
            finally:
                self._connected = False
            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    async def _open_price_loop(self) -> None:
        """Fetch Polymarket's official price to beat for the current window.

        Keeps asking until Polymarket publishes it (it can appear a little after the window opens).
        """
        attempts: dict[str, int] = {}
        while self._running:
            self._market_changed.clear()  # before reading the market, so a change during the fetch is not lost
            market = self._market
            if market is not None and market.market_id not in self._opens:
                tries = attempts.get(market.market_id, 0)
                if tries < OPEN_LOOKUP_MAX_ATTEMPTS:
                    attempts[market.market_id] = tries + 1
                    found = await self._fetch_open_price(market)
                    if found is not None:
                        self._opens[market.market_id] = found
                        self._trim_opens()
                    if len(attempts) > 48:
                        attempts.pop(next(iter(attempts)))
            try:
                await asyncio.wait_for(self._market_changed.wait(), timeout=OPEN_LOOKUP_RETRY_SECONDS)
            except TimeoutError:
                pass

    async def _fetch_open_price(self, market: MarketInfo) -> tuple[float, str] | None:
        if self._http is None:
            return None
        # 1) Gamma event metadata
        if market.event_slug:
            try:
                response = await self._http.get(
                    f"{self._settings.gamma_base_url}/events", params={"slug": market.event_slug}
                )
                response.raise_for_status()
                events = response.json()
                if isinstance(events, list) and events and isinstance(events[0], dict):
                    meta = events[0].get("eventMetadata")
                    if isinstance(meta, dict):
                        value = _to_float(meta.get("priceToBeat"))
                        if value is not None:
                            return value, "gamma"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("price-to-beat gamma lookup failed: %s", exc)
        # 2) Polymarket crypto-price endpoint (same data the website shows)
        variant = {300: "fiveminute", 900: "fifteen"}.get(int(market.duration_seconds))
        if variant:
            try:
                start = market.window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
                end = market.window_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                response = await self._http.get(
                    self._settings.crypto_price_api_url,
                    params={"symbol": "BTC", "eventStartTime": start, "variant": variant, "endDate": end},
                )
                response.raise_for_status()
                data = response.json()
                value = _to_float(data.get("openPrice")) if isinstance(data, dict) else None
                if value is not None:
                    return value, "polymarket"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("price-to-beat crypto-price lookup failed: %s", exc)
        return None
