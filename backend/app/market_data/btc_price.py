"""BTC/USD reference price feed and the per-window "price to beat".

Polymarket's 5-minute BTC markets resolve UP when the Chainlink BTC/USD *60-second TWAP* at the
window end is >= the same TWAP at the window start (the "price to beat"); this has been the rule
since 2026-08-14 (docs.polymarket.com changelog). The plain spot price is NOT what the site shows.
Only official Polymarket data is used here, with no estimates:
  * current price  -> Chainlink BTC/USD 60s TWAP stream from Polymarket's real-time data socket (RTDS,
                      topic ``crypto_prices_twap_sixty``)
  * price to beat  -> the event's ``eventMetadata.priceToBeat`` from the Gamma API (the value the
                      Polymarket event page displays)
Gamma only publishes ``eventMetadata.priceToBeat`` some time AFTER a window starts (sometimes only
after it closes), so on its own the dashboard shows no Price To Beat for the live window. The price to
beat is by definition the same TWAP60 series at the window boundary, so while Gamma has not answered
it is read from the RTDS tick stamped at the window start (same official Polymarket stream, never a
spot/exchange price or an estimate); Gamma's value replaces it as soon as it is published. If the
server starts mid-window the start tick was never seen, so the price to beat stays missing until Gamma
publishes it (no website/spot "open price" is ever substituted).
If neither is available it is shown as missing (reason in `last_error` / `price_to_beat_error`).

Besides the dashboard, ``gap_inputs`` feeds the risk manager's price-gap rule (buy only when BTC is
already ``min_price_gap_usd`` beyond the price to beat). It never raises; every failure degrades to
"no value" and the rule then fails closed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timezone
from collections import deque

import httpx
import websockets

from app import clock
from app.config.settings import Settings
from app.market_discovery.types import MarketInfo

logger = logging.getLogger(__name__)

STALE_AFTER_MS = 10_000
FIRST_TICK_TIMEOUT_SECONDS = 8.0  # no BTC tick this long after subscribing -> try the next subscription form
TICK_SILENCE_RECONNECT_SECONDS = 15.0  # stream was alive but went quiet -> reconnect
RTDS_OPEN_TOLERANCE_MS = 1_000  # max distance between the window start and the RTDS tick used as its open
TICK_HISTORY = 2_400  # ~40 minutes of one-per-second TWAP ticks kept to look up window-start values
OPEN_LOOKUP_RETRY_SECONDS = 3.0  # keeps asking until the official value is published
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}
# Every variant is the same official RTDS Chainlink BTC/USD (TWAP60) stream; they only differ in how the
# connection is opened, so a server/proxy quirk on one form cannot leave the price blank.
#   mode: 0 whole topic filtered client-side (verified live: streams every second), 1 same + Origin,
#         2 server-side btc/usd filter, 3 same + Origin (the server-side filter only returned one
#         history batch and no live updates when checked)
_WS_VARIANT_COUNT = 4
DEFAULT_TOPIC = "crypto_prices_twap_sixty"  # Chainlink BTC/USD 60s TWAP, the series Polymarket settles on


def _now_ms() -> int:
    return clock.now_ms()


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
        self._messages = 0  # BTC ticks received
        self._sub_mode = 0  # connection variant that last produced data
        self._beat_error: str | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._market: MarketInfo | None = None
        self._market_changed = asyncio.Event()
        # market_id -> (price, source) (source is always "polymarket")
        self._opens: dict[str, tuple[float, str]] = {}
        # recent RTDS TWAP60 ticks (ts_ms, price) and the window-start values read from them
        self._ticks: deque[tuple[int, float]] = deque(maxlen=TICK_HISTORY)
        self._rtds_opens: dict[str, tuple[float, str]] = {}
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
            headers=BROWSER_HEADERS,
            follow_redirects=True,
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
        official = self._opens.get(market.market_id)
        if official is not None:
            return official
        return self._rtds_open(market)

    def gap_inputs(self, market: MarketInfo | None) -> tuple[float | None, float | None, str]:
        """(current price, price to beat, reason) for the risk manager's price-gap rule.

        Both prices are the same official Chainlink TWAP60 series Polymarket settles on. A stale
        tick or a missing price to beat returns None for the unusable value plus a reason.
        """
        if not self._settings.btc_price_enabled:
            return None, None, "BTC price feed is disabled (BTC_PRICE_ENABLED=false)"
        latest = self._latest
        if latest is None:
            return None, None, "no BTC price tick received yet"
        age_ms = _now_ms() - latest[0]
        if age_ms > STALE_AFTER_MS:
            return None, None, f"BTC price is stale ({age_ms / 1000:.0f}s old)"
        beat = self.price_to_beat(market)
        if beat is None:
            return latest[1], None, "price to beat is not available yet"
        return latest[1], beat[0], ""

    def _rtds_open(self, market: MarketInfo) -> tuple[float, str] | None:
        """TWAP60 tick stamped at the window start (== Polymarket's price to beat), if we saw it."""
        cached = self._rtds_opens.get(market.market_id)
        if cached is not None:
            return cached
        start_ms = int(market.window_start.replace(tzinfo=timezone.utc).timestamp() * 1000)
        # The price to beat is the TWAP60 value stamped AT the window start: the first tick at or after it.
        # (Taking the nearest tick could pick the one just before the start, ~1 s older = about $1 off.)
        best: tuple[int, float] | None = None
        for ts_ms, price in self._ticks:
            if start_ms <= ts_ms <= start_ms + RTDS_OPEN_TOLERANCE_MS and (best is None or ts_ms < best[0]):
                best = (ts_ms, price)
        if best is None:
            latest = self._latest
            if latest is None or latest[0] <= start_ms + RTDS_OPEN_TOLERANCE_MS:
                return None  # the at/after-start tick may still arrive: wait for it instead of guessing
            # that tick was missed (e.g. a gap in the stream): fall back to the nearest tick, if close enough
            for ts_ms, price in self._ticks:
                distance = abs(ts_ms - start_ms)
                if distance <= RTDS_OPEN_TOLERANCE_MS and (best is None or distance < abs(best[0] - start_ms)):
                    best = (ts_ms, price)
        if best is None:
            return None
        found = (best[1], "polymarket-rtds")
        self._rtds_opens[market.market_id] = found
        while len(self._rtds_opens) > 24:
            self._rtds_opens.pop(next(iter(self._rtds_opens)))
        return found

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
            "price_source": "chainlink-twap60",
            "last_error": self._last_error,
            "ticks": self._messages,
            "reconnects": self._reconnects,
            "ws_mode": self._sub_mode,
            "price_to_beat_error": self._beat_error if beat is None else None,
        }

    # ----------------------------------------------------------------- internals
    def _trim_opens(self) -> None:
        while len(self._opens) > 24:
            self._opens.pop(next(iter(self._opens)))

    def _record(self, ts_ms: int, price: float) -> None:
        self._ticks.append((ts_ms, price))  # kept even if out of order: only used for window-start lookups
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
        # Only the Chainlink topic is ever subscribed, so the topic string is not checked: the
        # history batch can carry a different type/topic label than live updates.
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
            price = _to_float(row.get("price"))
        if price is None:
            return
        self._messages += 1
        self._last_error = None
        try:
            ts_ms = int(row.get("timestamp"))
        except (TypeError, ValueError):
            ts_ms = _now_ms()
        self._record(ts_ms, price)

    @staticmethod
    def _subscribe_frame(mode: int, topic: str = DEFAULT_TOPIC) -> str:
        """RTDS subscription frame. Modes 0/1 subscribe to the whole topic and rely on the
        client-side btc symbol filter in handle_message; modes 2/3 filter btc/usd server-side."""
        subscription: dict = {"topic": topic, "type": "*"}
        if mode >= 2:
            subscription["filters"] = json.dumps({"symbol": "btc/usd"})
        return json.dumps({"action": "subscribe", "subscriptions": [subscription]})

    def _connect(self, mode: int = 0):
        kwargs: dict = dict(
            # RTDS keeps the socket alive with the text "PING" frame (sent below); protocol-level
            # pings are not required and a missed pong must not drop a healthy stream.
            ping_interval=None,
            close_timeout=5,
            open_timeout=10,
            max_size=2**22,
        )
        if mode % 2 == 1:
            kwargs["origin"] = "https://polymarket.com"
        return websockets.connect(self._settings.rtds_ws_url, **kwargs)

    async def _stream(self) -> None:
        backoff = 0.5
        mode = self._sub_mode
        while self._running:
            got_tick = False
            try:
                async with self._connect(mode) as ws:
                    await ws.send(self._subscribe_frame(mode))
                    self._connected = True
                    logger.info("btc price ws connected (mode %s)", mode)
                    opened = time.monotonic()
                    ticks_seen = self._messages
                    last_tick = opened
                    next_ping = opened + 5.0
                    while self._running:
                        now = time.monotonic()
                        if now >= next_ping:
                            await ws.send("PING")
                            next_ping = now + 5.0
                        if self._messages != ticks_seen:
                            ticks_seen = self._messages
                            last_tick = now
                            if not got_tick:
                                got_tick = True
                                self._sub_mode = mode
                                backoff = 0.5
                                logger.info("btc price ws streaming (mode %s)", mode)
                        elif not got_tick and now - opened > FIRST_TICK_TIMEOUT_SECONDS:
                            raise RuntimeError(f"no BTC/USD ticks {FIRST_TICK_TIMEOUT_SECONDS:.0f}s after subscribing (mode {mode})")
                        elif got_tick and now - last_tick > TICK_SILENCE_RECONNECT_SECONDS:
                            raise RuntimeError("BTC/USD stream went silent")
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except (TimeoutError, asyncio.TimeoutError):
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        if not raw.strip() or raw.strip().upper() == "PONG":
                            continue
                        self.handle_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._reconnects += 1
                logger.warning("btc price ws error: %s", self._last_error)
                if not got_tick:
                    mode = (mode + 1) % _WS_VARIANT_COUNT  # nothing from this form: try the next one
            finally:
                self._connected = False
            if self._running:
                await asyncio.sleep(backoff if got_tick else 1.0)
                backoff = min(backoff * 2, 10.0)

    async def _open_price_loop(self) -> None:
        """Fetch Polymarket's official price to beat for the current window.

        Keeps asking until the website endpoint publishes it; there is no substitute value.
        """
        while self._running:
            self._market_changed.clear()  # before reading the market, so a change during the fetch is not lost
            market = self._market
            if market is not None and market.market_id not in self._opens:
                found = await self._fetch_open_price(market)
                if found is not None:
                    self._opens[market.market_id] = found
                    self._trim_opens()
                    self._beat_error = None
                    logger.info("price to beat %s = %s", market.slug, found[0])
            try:
                await asyncio.wait_for(self._market_changed.wait(), timeout=OPEN_LOOKUP_RETRY_SECONDS)
            except (TimeoutError, asyncio.TimeoutError):
                pass

    async def _fetch_open_price(self, market: MarketInfo) -> tuple[float, str] | None:
        """Official price to beat for this window (Gamma ``eventMetadata.priceToBeat``), else None.

        The website's ``crypto-price`` openPrice is deliberately NOT used: it is not the TWAP60 value
        Polymarket displays, so using it made the price to beat wrong when the server was started in the
        middle of a window (no RTDS window-start tick seen). Until Gamma publishes, the price to beat is
        shown as missing (and the price-gap rule fails closed) instead of showing a wrong number.
        """
        if self._http is None:
            return None
        return await self._fetch_open_price_gamma(market)

    async def _fetch_open_price_gamma(self, market: MarketInfo) -> tuple[float, str] | None:
        """``eventMetadata.priceToBeat`` of the event: the number the event page shows (TWAP60 at start)."""
        assert self._http is not None
        slug = market.event_slug or market.slug
        try:
            response = await self._http.get(
                f"{self._settings.gamma_base_url.rstrip('/')}/events",
                params={"slug": slug},
            )
            response.raise_for_status()
            data = response.json()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._beat_error = f"gamma: {type(exc).__name__}: {exc}"
            logger.warning("price-to-beat unavailable: %s", self._beat_error)
            return None
        events = data if isinstance(data, list) else [data]
        for event in events:
            if not isinstance(event, dict):
                continue
            holders = [event] + [m for m in (event.get("markets") or []) if isinstance(m, dict)]
            for holder in holders:
                metadata = holder.get("eventMetadata")
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = None
                if isinstance(metadata, dict):
                    value = _to_float(metadata.get("priceToBeat"))
                    if value is not None:
                        return value, "polymarket"
        self._beat_error = "gamma: priceToBeat not published yet"
        return None
