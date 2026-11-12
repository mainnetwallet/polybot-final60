"""FINAL_60_SECONDS_MARKET_BUY.

First 4 minutes: no trading.
Final N seconds (default 60): monitor UP and DOWN best asks.
best_ask >= threshold (default 0.80) => emit a marketable buy signal.

The threshold is a trigger, never a limit price. Signals carry the live ask so
the executor buys at the market instead of posting a fixed 0.80 order.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.config.runtime import RuntimeSettings
from app.market_data.book import OrderBook
from app.market_discovery.types import MarketInfo
from app.strategy.types import Signal, StrategyEvaluation


def datetime_to_epoch_ms(value: datetime) -> int:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return int(aware.timestamp() * 1000)


class FinalWindowStrategy:
    def evaluate(
        self,
        market: MarketInfo,
        books: dict[str, OrderBook],
        now_ms: int,
        config: RuntimeSettings,
    ) -> StrategyEvaluation:
        end_ms = datetime_to_epoch_ms(market.window_end)
        start_ms = datetime_to_epoch_ms(market.window_start)
        remaining = (end_ms - now_ms) / 1000.0

        up = books.get(market.up_token_id)
        down = books.get(market.down_token_id)
        up_ask = up.best_ask if up else None
        down_ask = down.best_ask if down else None
        up_armed = up_ask is not None and up_ask + 1e-9 >= config.trigger_threshold
        down_armed = down_ask is not None and down_ask + 1e-9 >= config.trigger_threshold

        if now_ms < start_ms:
            return StrategyEvaluation(
                False, remaining, up_ask, down_ask, up_armed, down_armed, reason="market_not_started"
            )
        if remaining <= 0:
            return StrategyEvaluation(
                False, remaining, up_ask, down_ask, up_armed, down_armed, reason="market_expired"
            )
        if remaining > config.final_window_seconds:
            return StrategyEvaluation(
                False, remaining, up_ask, down_ask, up_armed, down_armed, reason="outside_final_window"
            )

        signals: list[Signal] = []
        for side, market_side_token, book, ask in (
            ("UP", market.up_token_id, up, up_ask),
            ("DOWN", market.down_token_id, down, down_ask),
        ):
            if book is None or ask is None:
                continue
            if ask + 1e-9 < config.trigger_threshold:
                continue
            signals.append(
                Signal(
                    market_id=market.market_id,
                    side=side,
                    token_id=market_side_token,
                    best_ask=round(ask, 6),
                    ask_size=book.ask_size,
                    remaining_seconds=round(remaining, 3),
                    threshold=config.trigger_threshold,
                    observed_at_ms=now_ms,
                    book_age_ms=book.age_ms(now_ms),
                )
            )

        reason = "trigger" if signals else "monitoring"
        return StrategyEvaluation(
            True, remaining, up_ask, down_ask, up_armed, down_armed, signals, reason
        )


__all__ = ["FinalWindowStrategy", "datetime_to_epoch_ms"]
