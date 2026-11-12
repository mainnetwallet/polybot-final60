"""Persistent CLOB market websocket.

The strategy consumes every book event as it arrives. There is no polling in
the execution path; polling is limited to discovery and settlement checks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

import websockets

from app.config.settings import Settings
from app.market_data.book import OrderBook
from app.market_discovery.types import MarketInfo

logger = logging.getLogger(__name__)

BookCallback = Callable[[str, str, OrderBook], Awaitable[None]]


def now_ms() -> int:
    return int(time.time() * 1000)


class ClobMarketDataClient:
    def __init__(self, settings: Settings, on_book: BookCallback) -> None:
        self._settings = settings
        self._on_book = on_book
        self._desired: tuple[str, str, str] | None = None  # (market_id, up_token, down_token)
        self._market: MarketInfo | None = None
        self._books: dict[str, OrderBook] = {}
        self._side_by_token: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        self._connected = False
        self._last_message_ms = 0
        self._reconnects = 0
        self._last_error: str | None = None
        self._change = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def books(self) -> dict[str, OrderBook]:
        return self._books

    @property
    def status(self) -> dict:
        return {
            "connected": self._connected,
            "reconnects": self._reconnects,
            "last_message_ms": self._last_message_ms or None,
            "last_error": self._last_error,
        }

    def book_for(self, side: str) -> OrderBook | None:
        if not self._market:
            return None
        return self._books.get(self._market.token_for(side))

    def set_market(self, market: MarketInfo | None) -> None:
        desired = (market.market_id, market.up_token_id, market.down_token_id) if market else None
        if desired == self._desired:
            return
        self._desired = desired
        self._market = market
        self._side_by_token = (
            {market.up_token_id: "UP", market.down_token_id: "DOWN"} if market else {}
        )
        # Drop books that do not belong to the new market.
        self._books = {k: v for k, v in self._books.items() if k in self._side_by_token}
        self._change.set()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._supervise(), name="clob-market-ws")

    async def stop(self) -> None:
        self._running = False
        self._change.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False

    async def _supervise(self) -> None:
        backoff = 1.0
        while self._running:
            desired = self._desired
            if desired is None:
                self._change.clear()
                try:
                    await asyncio.wait_for(self._change.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                continue

            market_id, up_token, down_token = desired
            try:
                async with websockets.connect(
                    self._settings.clob_ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2**22,
                ) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "assets_ids": [up_token, down_token],
                                "type": "market",
                            }
                        )
                    )
                    self._connected = True
                    self._last_error = None
                    backoff = 1.0
                    logger.info("clob ws connected for market %s", market_id)
                    next_ping = time.monotonic() + self._settings.ws_heartbeat_seconds
                    while self._running and self._desired == desired:
                        if time.monotonic() >= next_ping:
                            try:
                                await ws.send("PING")
                            except Exception:
                                break
                            next_ping = time.monotonic() + self._settings.ws_heartbeat_seconds
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except TimeoutError:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        if raw.strip().upper() == "PONG" or not raw.strip():
                            continue
                        self._last_message_ms = now_ms()
                        await self._handle_raw(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._reconnects += 1
                logger.warning("clob ws error: %s", self._last_error)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
            finally:
                self._connected = False

    async def _handle_raw(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("non-json ws frame: %s", raw[:200])
            return
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if isinstance(message, dict):
                try:
                    await self._handle_message(message)
                except Exception as exc:  # never let one bad frame kill the stream
                    logger.warning("ws message handling failed: %s", exc)

    async def _handle_message(self, message: dict) -> None:
        event_type = message.get("event_type") or message.get("type")
        if event_type == "book":
            await self._apply_snapshot(message)
        elif event_type == "price_change":
            await self._apply_price_change(message)
        elif event_type == "best_bid_ask":
            await self._apply_best(message)
        elif event_type == "tick_size_change":
            logger.info("tick size change: %s", message)
        elif event_type == "last_trade_price":
            return
        elif event_type in {"subscribed", "pong"}:
            return
        elif event_type is not None:
            logger.debug("unhandled ws event %s", event_type)

    def _book(self, asset_id: str) -> OrderBook | None:
        if not asset_id or asset_id not in self._side_by_token:
            return None
        book = self._books.get(asset_id)
        if book is None:
            book = OrderBook(
                token_id=asset_id,
                market_id=self._market.market_id if self._market else "",
                side=self._side_by_token[asset_id],
            )
            self._books[asset_id] = book
        return book

    async def _apply_snapshot(self, message: dict) -> None:
        asset_id = str(message.get("asset_id") or "")
        book = self._book(asset_id)
        if book is None:
            return
        book.bids = self._levels(message.get("bids"))
        book.asks = self._levels(message.get("asks"))
        book.updated_at_ms = now_ms()
        book.sequence += 1
        await self._on_book(book.market_id, book.side, book)

    async def _apply_price_change(self, message: dict) -> None:
        changes = message.get("price_changes") or message.get("changes") or []
        if not isinstance(changes, list):
            return
        touched: dict[str, OrderBook] = {}
        for change in changes:
            if not isinstance(change, dict):
                continue
            asset_id = str(change.get("asset_id") or message.get("asset_id") or "")
            book = self._book(asset_id)
            if book is None:
                continue
            try:
                price = round(float(change.get("price")), 6)
                size = float(change.get("size"))
            except (TypeError, ValueError):
                continue
            side = str(change.get("side") or "").upper()
            target = book.bids if side == "BUY" else book.asks
            if size <= 0:
                target.pop(price, None)
            else:
                target[price] = size
            book.updated_at_ms = now_ms()
            book.sequence += 1
            touched[asset_id] = book

        # Some frames only carry best bid/ask; use them when present.
        asset_id = str(message.get("asset_id") or "")
        if asset_id and asset_id in self._side_by_token:
            book = self._book(asset_id)
            if book and (message.get("best_ask") is not None or message.get("best_bid") is not None):
                best_ask = self._to_float(message.get("best_ask"))
                best_bid = self._to_float(message.get("best_bid"))
                if best_ask and best_ask > 0:
                    book.asks.setdefault(best_ask, 0.0)  # size unknown until a sized event arrives
                    for stale in [p for p in book.asks if p < best_ask - 1e-9]:
                        book.asks.pop(stale, None)
                if best_bid and best_bid > 0:
                    book.bids.setdefault(best_bid, 0.0)
                    for stale in [p for p in book.bids if p > best_bid + 1e-9]:
                        book.bids.pop(stale, None)
                book.updated_at_ms = now_ms()
                book.sequence += 1
                touched[asset_id] = book

        for book in touched.values():
            await self._on_book(book.market_id, book.side, book)

    async def _apply_best(self, message: dict) -> None:
        asset_id = str(message.get("asset_id") or "")
        book = self._book(asset_id)
        if book is None:
            return
        best_ask = self._to_float(message.get("best_ask"))
        best_bid = self._to_float(message.get("best_bid"))
        ask_size = self._to_float(message.get("best_ask_size") or message.get("ask_size"))
        bid_size = self._to_float(message.get("best_bid_size") or message.get("bid_size"))
        if best_ask and best_ask > 0:
            book.asks = {p: s for p, s in book.asks.items() if p >= best_ask - 1e-9}
            book.asks[best_ask] = ask_size if ask_size is not None else book.asks.get(best_ask, 0.0)
        if best_bid and best_bid > 0:
            book.bids = {p: s for p, s in book.bids.items() if p <= best_bid + 1e-9}
            book.bids[best_bid] = bid_size if bid_size is not None else book.bids.get(best_bid, 0.0)
        book.updated_at_ms = now_ms()
        book.sequence += 1
        await self._on_book(book.market_id, book.side, book)

    @staticmethod
    def _levels(raw) -> dict[float, float]:
        levels: dict[float, float] = {}
        if isinstance(raw, list):
            for level in raw:
                if not isinstance(level, dict):
                    continue
                try:
                    price = round(float(level.get("price")), 6)
                    size = float(level.get("size"))
                except (TypeError, ValueError):
                    continue
                if size > 0:
                    levels[price] = size
        return levels

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None
