from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signal:
    market_id: str
    side: str  # UP | DOWN
    token_id: str
    best_ask: float
    ask_size: float | None
    remaining_seconds: float
    threshold: float
    observed_at_ms: int
    book_age_ms: float
    stage: int = 1  # 0 = early entry with take-profit sell, 1 = first entry (trigger), 2/3 = add-ons


@dataclass
class StrategyEvaluation:
    in_window: bool
    remaining_seconds: float
    up_ask: float | None
    down_ask: float | None
    up_armed: bool
    down_armed: bool
    signals: list[Signal] = field(default_factory=list)
    reason: str = ""

    def summary(self) -> dict:
        return {
            "in_window": self.in_window,
            "remaining_seconds": round(self.remaining_seconds, 2),
            "up_ask": self.up_ask,
            "down_ask": self.down_ask,
            "up_armed": self.up_armed,
            "down_armed": self.down_armed,
            "reason": self.reason,
        }
