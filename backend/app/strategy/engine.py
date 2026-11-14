"""FINAL_WINDOW_STAGED_MARKET_BUY.

First part of the market: no trading.
Final N seconds (default 80): monitor UP and DOWN best asks.

Entries are staged. Each stage may place ONE buy per market:

* stage 0 (optional, independent): while more than stage0_end_seconds (default 100) and at most stage0_start_seconds (default 200) remain
  (independent of the final window), stage0_min_ask (default 0.60) <= best_ask <= stage0_max_ask (default 0.85). The risk manager also
  demands the BTC price gap (default $50) and the order manager sells the shares again once the
  best bid is the tier's take-profit percentage above the buy price (15 / 10 / 5 % by buy price, capped at 0.99).

* stage 1: inside the final window, best_ask >= trigger_threshold (default 0.80).
  If the ask is not there yet the strategy keeps watching and fires the moment it
  gets there, any time before the market ends.
* stage 2: remaining <= stage2_seconds (default 50) and best_ask >= stage2_threshold
  (default 0.90).
* stage 3: remaining <= stage3_seconds (default 30) and best_ask >= stage3_threshold
  (default 0.90).

This module only produces the signals. The risk manager chains them: stage 1 can only buy while
more than stage3_seconds remain, stage 2 needs stage 1 to have bought before stage2_seconds
remained, and stage 3 is independent (see RiskManager._check_stage_chain).

The thresholds are triggers, never limit prices. Signals carry the live ask so the
executor buys at the market instead of posting a fixed-price order.
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


def stage_plan(config: RuntimeSettings) -> list[tuple[int, float, float]]:
    """(stage, max remaining seconds, ask threshold) for every entry stage."""
    return [
        (1, float(config.final_window_seconds), config.trigger_threshold),
        (2, float(config.stage2_seconds), config.stage2_threshold),
        (3, float(config.stage3_seconds), config.stage3_threshold),
    ]


def stage0_active(config: RuntimeSettings, remaining: float) -> bool:
    """Stage 0 buys between `stage0_start_seconds` left and `stage0_end_seconds` left (independent of the final window)."""
    return bool(
        config.stage0_enabled
        and config.stage0_end_seconds < remaining <= config.stage0_start_seconds
    )


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
        in_stage0 = stage0_active(config, remaining)
        if remaining > config.final_window_seconds and not in_stage0:
            return StrategyEvaluation(
                False, remaining, up_ask, down_ask, up_armed, down_armed, reason="outside_final_window"
            )

        signals: list[Signal] = []
        if in_stage0:
            for side, market_side_token, book, ask in (
                ("UP", market.up_token_id, up, up_ask),
                ("DOWN", market.down_token_id, down, down_ask),
            ):
                if book is None or ask is None:
                    continue
                if ask > config.stage0_max_ask + 1e-9 or ask + 1e-9 < config.stage0_min_ask:
                    continue
                signals.append(
                    Signal(
                        market_id=market.market_id,
                        side=side,
                        token_id=market_side_token,
                        best_ask=round(ask, 6),
                        ask_size=book.ask_size,
                        remaining_seconds=round(remaining, 3),
                        threshold=config.stage0_max_ask,
                        observed_at_ms=now_ms,
                        book_age_ms=book.age_ms(now_ms),
                        stage=0,
                    )
                )
        for stage, max_remaining, threshold in stage_plan(config):
            if remaining > max_remaining:
                continue
            for side, market_side_token, book, ask in (
                ("UP", market.up_token_id, up, up_ask),
                ("DOWN", market.down_token_id, down, down_ask),
            ):
                if book is None or ask is None:
                    continue
                if ask + 1e-9 < threshold:
                    continue
                signals.append(
                    Signal(
                        market_id=market.market_id,
                        side=side,
                        token_id=market_side_token,
                        best_ask=round(ask, 6),
                        ask_size=book.ask_size,
                        remaining_seconds=round(remaining, 3),
                        threshold=threshold,
                        observed_at_ms=now_ms,
                        book_age_ms=book.age_ms(now_ms),
                        stage=stage,
                    )
                )

        reason = "trigger" if signals else "monitoring"
        return StrategyEvaluation(
            True, remaining, up_ask, down_ask, up_armed, down_armed, signals, reason
        )


__all__ = ["FinalWindowStrategy", "datetime_to_epoch_ms", "stage0_active", "stage_plan"]
