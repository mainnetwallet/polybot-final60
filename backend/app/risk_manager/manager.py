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


class RiskManager:
    @staticmethod
    def _check_price_gap(signal: Signal, ctx: RiskContext, config: RuntimeSettings) -> RiskDecision | None:
        """Buy only when BTC is already >= min_price_gap_usd beyond the price to beat, in the
        direction being bought (UP above it, DOWN below it). Fails closed without price data."""
        min_gap = config.min_price_gap_usd
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
                f"{signal.side} needs {min_gap:.2f} in its direction",
            )
        return None

    def check(self, signal: Signal, ctx: RiskContext, config: RuntimeSettings) -> RiskDecision:
        if ctx.kill_switch:
            return RiskDecision(False, "kill_switch", "emergency kill switch is active")
        if not ctx.market_active:
            return RiskDecision(False, "market_inactive", "market is no longer active")
        if ctx.market_expired or ctx.remaining_seconds <= 0:
            return RiskDecision(False, "market_expired", "market window has expired")
        if ctx.remaining_seconds > config.final_window_seconds:
            return RiskDecision(False, "outside_final_window", "outside final trading window")
        if signal.book_age_ms > config.stale_data_max_age_ms:
            return RiskDecision(
                False, "stale_data", f"book age {signal.book_age_ms:.0f}ms exceeds limit"
            )
        if signal.best_ask + 1e-9 < config.trigger_threshold:
            return RiskDecision(False, "below_threshold", "ask below trigger threshold")
        if signal.best_ask > config.max_execution_price + 1e-9:
            return RiskDecision(
                False,
                "max_execution_price_exceeded",
                f"ask {signal.best_ask:.3f} above cap {config.max_execution_price:.3f}",
            )
        if signal.ask_size is None or signal.ask_size <= 0:
            return RiskDecision(False, "no_liquidity", "no ask size available at best price")
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
        add_on = signal.stage > 1 and signal.side in ctx.exposed_sides
        if ctx.open_positions >= config.max_open_positions and not add_on:
            return RiskDecision(False, "max_open_positions", "maximum open positions reached")
        return RiskDecision(True, "approved", "all risk checks passed")
