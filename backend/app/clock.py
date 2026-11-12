"""Official time reference.

The PC clock can drift a few seconds from real time, which made the dashboard countdown disagree with
the Polymarket page. Polymarket's CLOB ``GET /time`` returns the official unix time (seconds), so the
offset (official - local) is measured against it, using the round-trip midpoint, and every countdown /
window computation uses ``now_ms()`` / ``now_utc()`` from here.

Failure-safe: until a sample is taken (or if the endpoint is unreachable) the offset stays 0 (local clock).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 30.0
SAMPLES_PER_SYNC = 3
MAX_ABS_OFFSET_MS = 120_000  # ignore absurd results (bad endpoint) instead of shifting every window

_offset_ms: float = 0.0
_synced: bool = False
_last_error: str | None = None
_rtt_ms: float | None = None


def offset_ms() -> float:
    return _offset_ms


def now_ms() -> int:
    return int(time.time() * 1000 + _offset_ms)


def now_utc() -> datetime:
    """Corrected current time as a naive UTC datetime (same convention as the rest of the backend)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(milliseconds=_offset_ms)


def status() -> dict:
    return {"synced": _synced, "offset_ms": round(_offset_ms, 1), "rtt_ms": _rtt_ms, "error": _last_error}


def set_offset_ms(value: float) -> None:
    """Used by tests and by the sync loop."""
    global _offset_ms, _synced
    _offset_ms = float(value)
    _synced = True


def reset() -> None:
    global _offset_ms, _synced, _last_error, _rtt_ms
    _offset_ms, _synced, _last_error, _rtt_ms = 0.0, False, None, None


async def _sample(client: httpx.AsyncClient, url: str) -> tuple[float, float] | None:
    """One (offset_ms, rtt_ms) sample. The server time has 1 s resolution, so the second boundary is
    unknown: the server value is taken as the start of its second plus half a second."""
    t0 = time.time()
    response = await client.get(url)
    t1 = time.time()
    response.raise_for_status()
    text = response.text.strip()
    try:
        server_s = float(text)
    except ValueError:
        server_s = float(response.json())
    server_ms = server_s * 1000.0 + 500.0
    local_mid_ms = (t0 + t1) / 2.0 * 1000.0
    return server_ms - local_mid_ms, (t1 - t0) * 1000.0


async def sync_once(client: httpx.AsyncClient, base_url: str) -> bool:
    """Take a few samples and keep the median (robust to the 1 s resolution and a slow round trip)."""
    global _last_error, _rtt_ms
    url = f"{base_url.rstrip('/')}/time"
    samples: list[tuple[float, float]] = []
    for _ in range(SAMPLES_PER_SYNC):
        try:
            got = await _sample(client, url)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _last_error = f"{type(exc).__name__}: {exc}"
            continue
        if got is not None:
            samples.append(got)
        await asyncio.sleep(0.35)  # spreads samples across the 1 s resolution of the server time
    if not samples:
        return False
    offsets = sorted(o for o, _ in samples)
    median = offsets[len(offsets) // 2]
    if abs(median) > MAX_ABS_OFFSET_MS:
        _last_error = f"ignored offset {median:.0f}ms (out of range)"
        return False
    set_offset_ms(median)
    _rtt_ms = round(min(r for _, r in samples), 1)
    _last_error = None
    logger.info("clock offset vs polymarket: %+.0f ms (rtt %.0f ms)", median, _rtt_ms)
    return True


async def run(base_url: str) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0), follow_redirects=True) as client:
        while True:
            try:
                await sync_once(client, base_url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the clock task die
                logger.warning("clock sync failed: %s", exc)
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)
