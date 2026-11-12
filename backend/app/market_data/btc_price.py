"""BTC/USD reference price feed and the per-window "price to beat".

Polymarket's 5-minute BTC markets resolve UP when the Chainlink BTC/USD price at the
window end is >= the price at the window start (the "price to beat"). Only official
Polymarket data is used here, with no estimates:
  * current price  -> Chainlink BTC/USD stream from Polymarket's real-time data socket (RTDS)
  * price to beat  -> the crypto-price endpoint the Polymarket website itself calls
No fallback values: if the official value is not available yet it is shown as missing
(with the reason in `last_error` / `price_to_beat_error`), never guessed or substituted.

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
FIRST_TICK_TIMEOUT_SECONDS = 8.0  # no BTC tick this long after subscribing -> try the next subscription form
TICK_SILENCE_RECONNECT_SECONDS = 15.0  # stream was alive but went quiet -> reconnect
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
# Every variant is the same official RTDS Chainlink BTC/USD stream; they only differ in how the
# connection is opened, so a server/proxy quirk on one form cannot leave the price blank.
#   mode: 0 server-side btc/usd filter, 1 whole topic filtered client-side, 2/3 same but sending Origin
_WS_VARIANT_COUNT = 4


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
        self._messages = 0  # BTC ticks received
        self._sub_mode = 0  # connection variant that last produced data
        self._beat_error: str | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._market: MarketInfo | None = None
        self._market_changed = asyncio.Event()
        # market_id -> (price, source) (source is always "polymarket")
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
    def _subscribe_frame(mode: int) -> str:
        """RTDS subscription frame. Even modes filter btc/usd server-side; odd modes subscribe to
        the whole Chainlink topic and rely on the client-side symbol filter in handle_message."""
        subscription: dict = {"topic": "crypto_prices_chainlink", "type": "*"}
        if mode % 2 == 0:
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
        if mode >= 2:
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
        """Official value from the crypto-price endpoint the Polymarket website calls."""
        if self._http is None:
            return None
        variant = {300: "fiveminute", 900: "fifteen"}.get(int(market.duration_seconds))
        if not variant:
            self._beat_error = f"unsupported window length {int(market.duration_seconds)}s"
            return None
        error: str | None = None
        for suffix in ("Z", ".000Z"):  # the endpoint's accepted timestamp form; try both
            start = market.window_start.strftime("%Y-%m-%dT%H:%M:%S") + suffix
            end = market.window_end.strftime("%Y-%m-%dT%H:%M:%S") + suffix
            try:
                response = await self._http.get(
                    self._settings.crypto_price_api_url,
                    params={"symbol": "BTC", "eventStartTime": start, "variant": variant, "endDate": end},
                )
                response.raise_for_status()
                data = response.json()
                value = _to_float(data.get("openPrice")) if isinstance(data, dict) else None
                if value is not None:
                    return value, "polymarket"
                error = "crypto-price has no openPrice yet"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = f"crypto-price: {type(exc).__name__}: {exc}"
        self._beat_error = error
        logger.warning("price-to-beat unavailable: %s", error)
        return None
