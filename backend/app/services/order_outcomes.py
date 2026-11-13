"""Per-stage result of a BUY order (WIN / LOSS / OPEN) and its own PnL.

Stages share one Position per (market, side), but every stage trades and exits on its own (own
take-profit, own stop-loss). So a buy's result must come from the shares *its stage* bought and sold,
not from the whole position: a stopped-out stage 1 must not turn the still-open stage 2 into a LOSS.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

DUST = 0.02


def stage_outcomes(
    orders: Iterable,
    positions: dict,
    key_stage: Callable[[str | None], int],
    key_action: Callable[[str | None], str],
) -> dict[tuple[str, str, int], tuple[str | None, float | None]]:
    """{(market_id, side, stage): (result, pnl)} for every stage that has a filled buy."""
    groups: dict[tuple[str, str, int], dict[str, list]] = defaultdict(lambda: {"buys": [], "sells": []})
    for o in orders:
        if (o.filled_size or 0) <= 0:
            continue
        kind = "sells" if key_action(o.order_key) == "SELL" else "buys"
        groups[(o.market_id, o.side, key_stage(o.order_key))][kind].append(o)

    out: dict[tuple[str, str, int], tuple[str | None, float | None]] = {}
    for (market_id, side, stage), g in groups.items():
        buys, sells = g["buys"], g["sells"]
        bought = sum(o.filled_size for o in buys)
        if bought <= 0:
            continue
        cost_ps = sum(o.filled_size * (o.avg_fill_price or 0.0) for o in buys) / bought
        fee_ps = sum(o.fee or 0.0 for o in buys) / bought
        sold = min(sum(o.filled_size for o in sells), bought)
        proceeds = sum(o.filled_size * (o.avg_fill_price or 0.0) for o in sells)
        sell_fees = sum(o.fee or 0.0 for o in sells)
        pnl = proceeds - sell_fees - sold * (cost_ps + fee_ps)
        remaining = bought - sold
        if remaining < DUST:  # this stage sold everything itself (take-profit or stop-loss)
            out[(market_id, side, stage)] = ("WIN" if pnl > 0 else "LOSS", round(pnl, 6))
            continue
        position = positions.get((market_id, side))
        if position is None or position.status != "closed":
            out[(market_id, side, stage)] = ("OPEN", None)
            continue
        if position.resolution is None:
            out[(market_id, side, stage)] = (None, None)  # closed some other way: nothing reliable to say
            continue
        payout = remaining * (1.0 if position.resolution == side else 0.0)
        pnl += payout - remaining * (cost_ps + fee_ps)
        out[(market_id, side, stage)] = ("WIN" if pnl > 0 else "LOSS", round(pnl, 6))
    return out


def sell_outcomes(
    orders: Iterable,
    key_stage: Callable[[str | None], int],
    key_action: Callable[[str | None], str],
) -> dict[int, tuple[str, float]]:
    """{order id: (result, pnl)} for every filled SELL (take-profit or stop-loss exit).

    A SELL row used to show no result at all, so a stop-loss that lost money looked like an empty
    row. Its own P&L is what that exit realised against the cost of the shares the same stage
    bought (buy fee included): proceeds - sell fee - shares * (cost + buy fee per share).
    """
    buys: dict[tuple[str, str, int], list] = defaultdict(list)
    sells: list = []
    for o in orders:
        if (o.filled_size or 0) <= 0:
            continue
        if key_action(o.order_key) == "SELL":
            sells.append(o)
        else:
            buys[(o.market_id, o.side, key_stage(o.order_key))].append(o)

    out: dict[int, tuple[str, float]] = {}
    for o in sells:
        group = buys.get((o.market_id, o.side, key_stage(o.order_key)))
        if not group:
            continue
        bought = sum(b.filled_size for b in group)
        if bought <= 0:
            continue
        cost_ps = sum(b.filled_size * (b.avg_fill_price or 0.0) for b in group) / bought
        fee_ps = sum(b.fee or 0.0 for b in group) / bought
        shares = min(o.filled_size, bought)
        pnl = o.filled_size * (o.avg_fill_price or 0.0) - (o.fee or 0.0) - shares * (cost_ps + fee_ps)
        out[o.id] = ("WIN" if pnl > 0 else "LOSS", round(pnl, 6))
    return out
