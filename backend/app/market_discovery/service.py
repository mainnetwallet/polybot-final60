"""Gamma API discovery for the BTC 5-minute UP/DOWN series.

Discovery never drives entry timing; it only resolves which market/token pair
should be streamed. Trade triggers are event-driven from the CLOB websocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.config.settings import Settings
from app.market_discovery.types import MarketInfo, ResolutionInfo, parse_iso, to_utc_naive

logger = logging.getLogger(__name__)


class DiscoveryError(RuntimeError):
    pass


class MarketDiscovery:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._series_id: str | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        headers = {"User-Agent": self._settings.http_user_agent, "Accept": "application/json"}
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.gamma_base_url,
                timeout=httpx.Timeout(15.0),
                headers=headers,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict | None = None) -> object:
        if self._client is None:
            await self.start()
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(path, params=params)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # network / rate limit
                last_error = exc
                await asyncio.sleep(0.4 * (attempt + 1))
        raise DiscoveryError(f"Gamma request failed: {path}: {last_error}")

    async def series_id(self) -> str:
        async with self._lock:
            if self._series_id:
                return self._series_id
            data = await self._get("/series", {"slug": self._settings.series_slug})
            if not isinstance(data, list) or not data:
                raise DiscoveryError(f"series {self._settings.series_slug!r} not found")
            self._series_id = str(data[0]["id"])
            logger.info("resolved series %s -> id %s", self._settings.series_slug, self._series_id)
            return self._series_id

    def _window_seconds(self) -> int:
        """Window length from the series slug (``...-5m`` -> 300, ``...-15m`` -> 900)."""
        match = re.search(r"-(\d+)m$", self._settings.series_slug)
        return int(match.group(1)) * 60 if match else 300

    async def get_active_market(self, now: datetime | None = None) -> MarketInfo | None:
        """Find the market whose window contains ``now``.

        Three lookups, most reliable first. A lookup that errors or finds nothing falls
        through to the next one; only if every lookup errors is an error raised.
        """
        now = to_utc_naive(now or datetime.now(timezone.utc))
        window = self._window_seconds()
        errors: list[Exception] = []

        # 1) Deterministic slug: <prefix>-<window start unix>. One small request.
        epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
        prefix = self._settings.event_slug_prefix
        for window_start in (epoch - epoch % window, epoch - epoch % window - window):
            try:
                events = await self._get("/events", {"slug": f"{prefix}-{window_start}"})
            except DiscoveryError as exc:
                errors.append(exc)
                break
            if isinstance(events, list):
                market = self._pick_active(events, now)
                if market is not None:
                    return market

        # 2) Series filter on /events: every open event of the series, soonest end first.
        try:
            events = await self._get(
                "/events",
                {
                    "series_slug": self._settings.series_slug,
                    "closed": "false",
                    "limit": 100,
                    "order": "endDate",
                    "ascending": "true",
                },
            )
            if isinstance(events, list):
                market = self._pick_active(events, now)
                if market is not None:
                    return market
        except DiscoveryError as exc:
            errors.append(exc)

        # 3) Legacy: events embedded in the series detail (can lag behind or be truncated).
        try:
            sid = await self.series_id()
            series = await self._get(f"/series/{sid}")
            events = series.get("events") if isinstance(series, dict) else None
            if isinstance(events, list):
                return self._pick_active(events, now)
        except DiscoveryError as exc:
            errors.append(exc)

        if errors and len(errors) >= 3:
            raise DiscoveryError(f"all market lookups failed: {errors[-1]}")
        return None

    def _pick_active(self, events: list, now: datetime) -> MarketInfo | None:
        candidates: list[MarketInfo] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            markets = event.get("markets") or []
            if not markets:
                continue
            market = markets[0]
            if market.get("closed"):
                continue
            start = (
                parse_iso(event.get("startTime"))
                or parse_iso(event.get("startDate"))
                or parse_iso(market.get("eventStartTime"))
            )
            end = parse_iso(market.get("endDate")) or parse_iso(event.get("endDate"))
            slug_start = self._start_from_slug(str(event.get("slug") or market.get("slug") or ""))
            if end is None and slug_start is not None:
                end = slug_start + timedelta(seconds=300)
            if slug_start is not None and end is not None and 60 <= (end - slug_start).total_seconds() <= 900:
                start = slug_start  # the slug carries the exact window start
            if start is None or end is None:
                continue
            if not (start <= now < end):
                continue
            series_info = (event.get("series") or [{}])[0]
            sid = str(series_info.get("id") or self._series_id or self._settings.series_slug)
            info = self._build_market(event, market, sid, start, end)
            if info is not None:
                candidates.append(info)

        if not candidates:
            return None
        candidates.sort(key=lambda m: m.window_start)
        return candidates[0]

    @staticmethod
    def _start_from_slug(slug: str) -> datetime | None:
        """Slugs look like btc-updown-5m-<window_start_unix>."""
        tail = slug.rsplit("-", 1)[-1]
        if not tail.isdigit():
            return None
        try:
            return to_utc_naive(datetime.fromtimestamp(int(tail), tz=timezone.utc))
        except (OverflowError, OSError, ValueError):
            return None

    def _build_market(
        self, event: dict, market: dict, series_id: str, start: datetime, end: datetime
    ) -> MarketInfo | None:
        market_id = market.get("conditionId") or market.get("id")
        if not market_id:
            return None
        try:
            outcomes = json.loads(market.get("outcomes") or "[]")
            version = 2 if str(market.get("version") or "v1").lower() == "v2" else 1
            raw_asset_ids = market.get("positionIds") if version == 2 else market.get("clobTokenIds")
            asset_ids = json.loads(raw_asset_ids) if isinstance(raw_asset_ids, str) else raw_asset_ids
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(outcomes, list) or not isinstance(asset_ids, list):
            return None
        if len(outcomes) != 2 or len(asset_ids) != 2:
            return None

        mapping = {str(name).strip().lower(): str(asset_id) for name, asset_id in zip(outcomes, asset_ids)}
        up = mapping.get("up")
        down = mapping.get("down")
        if not up or not down:
            return None

        def _float(value, default: float) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        return MarketInfo(
            market_id=str(market_id),
            slug=str(market.get("slug") or ""),
            event_slug=str(event.get("slug") or ""),
            series_id=series_id,
            window_start=start,
            window_end=end,
            up_token_id=up,
            down_token_id=down,
            tick_size=_float(market.get("orderPriceMinTickSize"), 0.01),
            min_order_size=_float(market.get("orderMinSize"), 5.0),
            asset_version=version,
        )

    @staticmethod
    def _side_from_label(label: object) -> str | None:
        text = str(label or "").strip().lower()
        if text == "up":
            return "UP"
        if text == "down":
            return "DOWN"
        return None

    async def _gamma_resolution(self, market: MarketInfo) -> ResolutionInfo:
        """Gamma fallback. ``closed`` alone only means trading stopped; need 1/0 prices."""
        if not market.event_slug:
            return ResolutionInfo(market.market_id, False, None)
        try:
            events = await self._get("/events", {"slug": market.event_slug})
        except DiscoveryError:
            return ResolutionInfo(market.market_id, False, None)
        if not isinstance(events, list) or not events:
            return ResolutionInfo(market.market_id, False, None)
        event = events[0]
        market_data = (event.get("markets") or [{}])[0]
        if not market_data.get("closed"):
            return ResolutionInfo(market.market_id, False, None)
        try:
            outcomes = json.loads(market_data.get("outcomes") or "[]")
            prices = json.loads(market_data.get("outcomePrices") or "[]")
            prices = [float(p) for p in prices]
        except (TypeError, ValueError, json.JSONDecodeError):
            return ResolutionInfo(market.market_id, True, None)
        if len(outcomes) != 2 or len(prices) != 2:
            return ResolutionInfo(market.market_id, True, None)
        for name, price in zip(outcomes, prices):
            if price >= 0.99:
                side = self._side_from_label(name)
                if side is not None:
                    return ResolutionInfo(market.market_id, True, side)
        return ResolutionInfo(market.market_id, True, None)

    async def get_resolution(self, market: MarketInfo) -> ResolutionInfo:
        """Resolve a finished market from Gamma's documented event-by-slug response."""
        return await self._gamma_resolution(market)


class MarketMonitor:
    """Keeps the active market current. Polling here is discovery only."""

    GIVE_UP_SECONDS = 6 * 3600

    def __init__(self, discovery: MarketDiscovery, settings: Settings, on_market) -> None:
        self._discovery = discovery
        self._settings = settings
        self._on_market = on_market
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_market: MarketInfo | None = None
        self._open_events: dict[str, MarketInfo] = {}

    @property
    def current(self) -> MarketInfo | None:
        return self._last_market

    @property
    def open_markets(self) -> dict[str, MarketInfo]:
        return dict(self._open_events)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="market-monitor")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                market = await self._discovery.get_active_market()
                if market and (self._last_market is None or market.market_id != self._last_market.market_id):
                    self._last_market = market
                    self._open_events[market.slug] = market
                    await self._on_market(market, "opened")
                elif market is None and self._last_market is not None:
                    # Keep the finished market visible for settlement, stop treating it as active.
                    self._last_market = None
                    await self._on_market(None, "gap")
                elif market is not None:
                    self._open_events[market.slug] = market
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("market discovery failed: %s", exc)
            await asyncio.sleep(self._settings.discovery_refresh_seconds)

    async def poll_resolution(self) -> list[ResolutionInfo]:
        """Check finished markets. A market stays tracked until its outcome is known.

        Previously a market whose Gamma ``closed`` flag flipped before the winner was
        published was dropped here and never checked again, so its position stayed
        open (shown as "Unrealized") forever and the balance was never credited.
        """
        results: list[ResolutionInfo] = []
        for slug, market in list(self._open_events.items()):
            if market.remaining_seconds() > -5:
                continue
            info = await self._discovery.get_resolution(market)
            if info.outcome:
                results.append(info)
                self._open_events.pop(slug, None)
            elif info.closed:
                results.append(info)  # closed but winner not published yet: keep polling
            elif market.remaining_seconds() < -self.GIVE_UP_SECONDS:
                self._open_events.pop(slug, None)
        return results

    def track(self, market: MarketInfo) -> None:
        self._open_events[market.slug] = market

    def is_tracked(self, slug: str) -> bool:
        return slug in self._open_events


__all__ = [
    "DiscoveryError",
    "MarketDiscovery",
    "MarketInfo",
    "MarketMonitor",
    "ResolutionInfo",
    "time",
]
