"""BTC/USD reference price feed and the per-window "price to beat".

Polymarket's 5-minute BTC markets resolve UP when the Chainlink BTC/USD price at the
window end is >= the price at the window start (the "price to beat"). This feed
streams that same Chainlink price from Polymarket's real-time data socket (RTDS) so
the dashboard can show the live price, the price to beat and the gap between them.

It is display-only: nothing here feeds the strategy or order path, and every failure
degrades to "no value" instead of raising.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import timezone

import httpx
import websockets

from app.config.settings import Settings
from app.market_discovery.types import MarketInfo

logger = logging.getLogger(__name__)

# A tick this close to the window start is accepted as the window's opening price.
OPEN_TICK_TOLERANCE_MS = 3000
HISTORY_TICKS = 1800  # ~30 min at 1 tick/sec, enough to cover several windows
STALE_AFTER_MS = 10_000
OPEN_LOOKUP_RETRY_SECONDS = 4.0
OPEN_LOOKUP_MAX_ATTEMPTS = 8


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _window_start_ms(market: MarketInfo) -> int:
    return int(market.window_start.replace(tzinfo=timezone.utc).timestamp() * 1000)


class BtcPriceFeed:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._history: deque[tuple[int, float]] = deque(maxlen=HISTORY_TICKS)
        self._connected = False
        self._last_error: str | None = None
        self._reconnects = 0
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._market: MarketInfo | None = None
        self._market_changed = asyncio.Event()
        # market_id -> (price, source)
        self._opens: dict[str, tuple[float, str]] = {}
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ public
    @property
    def latest(self) -> tuple[int, float] | None:
        return self._history[-1] if self._history else None

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
        if market is None:
            return None
        cached = self._opens.get(market.market_id)
        if cached is not None:
            return cached
        tick = self._open_from_history(_window_start_ms(market))
        if tick is not None:
            self._opens[market.market_id] = (tick, "feed")
            self._trim_opens()
            return self._opens[market.market_id]
        return None

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
    def _open_from_history(self, start_ms: int) -> float | None:
        best: tuple[int, float] | None = None
        for ts, price in self._history:
            if best is None or abs(ts - start_ms) < abs(best[0] - start_ms):
                best = (ts, price)
        if best is not None and abs(best[0] - start_ms) <= OPEN_TICK_TOLERANCE_MS:
            return best[1]
        return None

    def _trim_opens(self) -> None:
        while len(self._opens) > 24:
            self._opens.pop(next(iter(self._opens)))

    def _record(self, ts_ms: int, price: float) -> None:
        if self._history and ts_ms < self._history[-1][0]:
            return  # out-of-order tick
        self._history.append((ts_ms, price))

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
        """If the bot joined mid-window there is no opening tick; ask Polymarket for it."""
        attempts: dict[str, int] = {}
        while self._running:
            market = self._market
            if market is not None and self.price_to_beat(market) is None:
                tries = attempts.get(market.market_id, 0)
                if tries < OPEN_LOOKUP_MAX_ATTEMPTS:
                    attempts[market.market_id] = tries + 1
                    found = await self._fetch_open_price(market)
                    if found is not None:
                        self._opens[market.market_id] = found
                        self._trim_opens()
                    if len(attempts) > 48:
                        attempts.pop(next(iter(attempts)))
            self._market_changed.clear()
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
