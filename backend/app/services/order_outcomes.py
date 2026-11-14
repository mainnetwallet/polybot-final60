"""WIN / LOSS and PnL of a filled SELL order (take-profit or stop-loss exit).

Only SELL rows carry a result. A BUY row stays blank: its outcome is the exit that followed, which the
SELL row already shows, so showing it on both rows duplicated the same PnL.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable


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
