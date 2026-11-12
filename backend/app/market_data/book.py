from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class OrderBook:
    token_id: str
    market_id: str
    side: str  # UP | DOWN
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    updated_at_ms: int = 0
    sequence: int = 0
    source: str = "clob_ws"

    @property
    def best_bid(self) -> float | None:
        prices = [price for price, size in self.bids.items() if size > 0]
        return max(prices) if prices else None

    @property
    def best_ask(self) -> float | None:
        prices = [price for price, size in self.asks.items() if size > 0]
        return min(prices) if prices else None

    @property
    def bid_size(self) -> float | None:
        best = self.best_bid
        return self.bids.get(best) if best is not None else None

    @property
    def ask_size(self) -> float | None:
        best = self.best_ask
        return self.asks.get(best) if best is not None else None

    def age_ms(self, now_ms: int | None = None) -> float:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        return max(0.0, float(now_ms - self.updated_at_ms)) if self.updated_at_ms else float("inf")

    def is_fresh(self, max_age_ms: float, now_ms: int | None = None) -> bool:
        return self.updated_at_ms > 0 and self.age_ms(now_ms) <= max_age_ms

    def level_prices(self, up_to: float | None = None) -> list[tuple[float, float]]:
        prices = sorted(p for p, size in self.asks.items() if size > 0)
        if up_to is not None:
            prices = [p for p in prices if p <= up_to + 1e-9]
        return [(p, self.asks[p]) for p in prices]

    def snapshot(self) -> dict:
        return {
            "token_id": self.token_id,
            "market_id": self.market_id,
            "side": self.side,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "updated_at_ms": self.updated_at_ms,
            "age_ms": round(self.age_ms(), 1) if self.updated_at_ms else None,
            "source": self.source,
        }
