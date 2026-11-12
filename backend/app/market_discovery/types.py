from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return to_utc_naive(datetime.fromisoformat(text))
    except ValueError:
        return None


@dataclass(frozen=True)
class MarketInfo:
    market_id: str
    slug: str
    event_slug: str
    series_id: str
    window_start: datetime  # UTC naive
    window_end: datetime  # UTC naive
    up_token_id: str
    down_token_id: str
    tick_size: float = 0.01
    min_order_size: float = 5.0
    status: str = "active"

    @property
    def duration_seconds(self) -> float:
        return (self.window_end - self.window_start).total_seconds()

    def remaining_seconds(self, now: datetime | None = None) -> float:
        now = to_utc_naive(now or datetime.now(timezone.utc))
        return (self.window_end - now).total_seconds()

    def token_for(self, side: str) -> str:
        return self.up_token_id if side == "UP" else self.down_token_id


@dataclass(frozen=True)
class ResolutionInfo:
    market_id: str
    closed: bool
    outcome: str | None  # UP | DOWN | None
