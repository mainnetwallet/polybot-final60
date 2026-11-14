from __future__ import annotations

from dataclasses import dataclass

from app.config.runtime import RuntimeSettings
from app.strategy.types import Signal


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    detail: str = ""


@dataclass
class RiskContext:
    now_ms: int
    kill_switch: bool
    account_balance: float
    daily_pnl: float
    trades_today: int
    open_positions: int
    market_active: bool
    market_expired: bool
    remaining_seconds: float
    # sides already held / working in this market; lets stage 2/3 add to a position
    exposed_sides: frozenset[str] = frozenset()
    # Current BTC price and the window's price to beat (same Chainlink TWAP60 series), for the
    # price-gap rule. None = unavailable; price_gap_issue says why.
    btc_price: float | None = None
    price_to_beat: float | None = None
    price_gap_issue: str = ""
    # Stage chain: did stage 1 already buy in this market, and how many seconds were left when it
    # did (None = unknown, e.g. after a restart; then stage 2 stays blocked, fail closed).
    stage1_bought: bool = False
    stage1_remaining: float | None = None
    # Sides whose open position in this market is nothing but a Stage 0 holding. Stage 0 manages and
    # sells its own shares, so they do not count against max_open_positions for stages 1-3.
    stage0_pure_sides: frozenset[str] = frozenset()


class RiskManager:
    @staticmethod
    def _check_stage_chain(signal: Signal, ctx: RiskContext, config: RuntimeSettings) -> RiskDecision | None:
        """Entry timing per stage (always on). Stages are independent: no stage needs another one.

        * stage 1: can only buy while MORE than stage3_seconds (30s) remain; this is its own cutoff.
        * stage 2 and stage 3: buy whenever their own rules (time, ask, price gap, ...) are met,
          whether or not stage 1 (or 2) bought or was stopped out.
        """
        if signal.stage == 1:
            if not ctx.stage1_bought and ctx.remaining_seconds <= config.stage3_seconds:
                return RiskDecision(
                    False,
                    "stage1_missed",
                    f"stage 1 did not buy before the last {config.stage3_seconds}s; stage 1 is off this market",
                )
        return None

    @staticmethod
    def _check_price_gap(signal: Signal, ctx: RiskContext, config: RuntimeSettings) -> RiskDecision | None:
        """Buy only when BTC is already >= min_price_gap_usd beyond the price to beat, in the
        direction being bought (UP above it, DOWN below it). Fails closed without price data."""
        min_gap = {
            0: config.stage0_min_price_gap_usd,
            2: config.stage2_min_price_gap_usd,
            3: config.stage3_min_price_gap_usd,
        }.get(signal.stage, config.min_price_gap_usd)
        if min_gap <= 0:
            return None
        if ctx.btc_price is None or ctx.price_to_beat is None:
            return RiskDecision(
                False,
                "price_gap_unavailable",
                ctx.price_gap_issue or "BTC price or price to beat is unavailable",
            )
        move = ctx.btc_price - ctx.price_to_beat
        directional = move if signal.side == "UP" else -move
        if directional + 1e-9 < min_gap:
            return RiskDecision(
                False,
                "price_gap_too_small",
                f"BTC {ctx.btc_price:.2f} is {move:+.2f} from price to beat {ctx.price_to_beat:.2f}; "
                f"stage {signal.stage} {signal.side} needs {min_gap:.2f} in its direction",
            )
        return None

    def check(self, signal: Signal, ctx: RiskContext, config: RuntimeSettings) -> RiskDecision:
        if ctx.kill_switch:
            return RiskDecision(False, "kill_switch", "emergency kill switch is active")
        if not ctx.market_active:
            return RiskDecision(False, "market_inactive", "market is no longer active")
        if ctx.market_expired or ctx.remaining_seconds <= 0:
            return RiskDecision(False, "market_expired", "market window has expired")
        if signal.stage == 0:
            if not config.stage0_enabled:
                return RiskDecision(False, "stage0_disabled", "stage 0 is switched off")
            if not (config.stage0_end_seconds < ctx.remaining_seconds <= config.stage0_start_seconds):
                return RiskDecision(
                    False,
                    "outside_stage0_window",
                    f"stage 0 buys only between {config.stage0_start_seconds}s and "
                    f"{config.stage0_end_seconds}s left",
                )
        elif ctx.remaining_seconds > config.final_window_seconds:
            return RiskDecision(False, "outside_final_window", "outside final trading window")
        if signal.book_age_ms > config.stale_data_max_age_ms:
            return RiskDecision(
                False, "stale_data", f"book age {signal.book_age_ms:.0f}ms exceeds limit"
            )
        if signal.stage == 0:
            if signal.best_ask + 1e-9 < config.stage0_min_ask:
                return RiskDecision(
                    False,
                    "stage0_ask_too_low",
                    f"ask {signal.best_ask:.3f} below the stage 0 minimum {config.stage0_min_ask:.3f}",
                )
            if signal.best_ask > config.stage0_max_ask + 1e-9:
                return RiskDecision(
                    False,
                    "stage0_ask_too_high",
                    f"ask {signal.best_ask:.3f} above the stage 0 limit {config.stage0_max_ask:.3f}",
                )
            if config.stage0_high_buy_too_early(signal.best_ask, ctx.remaining_seconds):
                return RiskDecision(
                    False,
                    "stage0_high_buy_too_early",
                    f"ask {signal.best_ask:.3f} is {config.stage0_high_buy_price:.2f} or more: "
                    f"stage 0 buys that only from {config.stage0_high_buy_opens_at():g}s left",
                )
        else:
            # each entry stage has its own ask threshold (stage 1 = trigger_threshold)
            stage_threshold = {
                2: config.stage2_threshold,
                3: config.stage3_threshold,
            }.get(signal.stage, config.trigger_threshold)
            if signal.best_ask + 1e-9 < stage_threshold:
                return RiskDecision(
                    False,
                    "below_threshold",
                    f"ask {signal.best_ask:.3f} below stage {signal.stage} threshold {stage_threshold:.3f}",
                )
            if signal.best_ask > config.max_execution_price + 1e-9:
                return RiskDecision(
                    False,
                    "max_execution_price_exceeded",
                    f"ask {signal.best_ask:.3f} above cap {config.max_execution_price:.3f}",
                )
        if signal.ask_size is None or signal.ask_size <= 0:
            return RiskDecision(False, "no_liquidity", "no ask size available at best price")
        chain = self._check_stage_chain(signal, ctx, config)
        if chain is not None:
            return chain
        gap = self._check_price_gap(signal, ctx, config)
        if gap is not None:
            return gap
        notional = config.trade_amount_usdc
        if ctx.account_balance + 1e-9 < notional:
            return RiskDecision(
                False,
                "insufficient_balance",
                f"balance {ctx.account_balance:.2f} < trade amount {notional:.2f}",
            )
        if config.max_daily_loss > 0 and ctx.daily_pnl <= -config.max_daily_loss + 1e-9:
            return RiskDecision(
                False, "daily_loss_limit", f"daily pnl {ctx.daily_pnl:.2f} hit stop loss"
            )
        if config.daily_profit_target > 0 and ctx.daily_pnl >= config.daily_profit_target - 1e-9:
            return RiskDecision(
                False, "daily_profit_target", f"daily pnl {ctx.daily_pnl:.2f} hit target"
            )
        if ctx.trades_today >= config.max_trades_per_day:
            return RiskDecision(False, "max_trades_per_day", "daily trade cap reached")
        exposed_sides = ctx.exposed_sides
        open_positions = ctx.open_positions
        if signal.stage != 0 and ctx.stage0_pure_sides:
            # a pure Stage 0 holding belongs to its own take-profit system
            exposed_sides = exposed_sides - ctx.stage0_pure_sides
            open_positions -= len(ctx.stage0_pure_sides & ctx.exposed_sides)
        add_on = signal.stage > 1 and signal.side in exposed_sides
        if open_positions >= config.max_open_positions and not add_on:
            return RiskDecision(False, "max_open_positions", "maximum open positions reached")
        return RiskDecision(True, "approved", "all risk checks passed")
