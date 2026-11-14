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


def stage_stats(
    orders: Iterable,
    key_stage: Callable[[str | None], int],
    key_action: Callable[[str | None], str],
    stages: tuple[int, ...] = (0, 1, 2, 3),
) -> list[dict]:
    """Per-stage WIN / LOSS count and PnL of the exits that already happened.

    A trade (one stage's shares in one market and side) is counted only once it has been sold: by its
    take-profit, its stop-loss or the operator's manual sell. Shares still held, or held to settlement,
    are not counted. A trade with several sell orders (partial fills) is one trade: its PnL is the
    sum of its sells, a WIN when that sum is above zero.
    """
    orders = list(orders)
    outcomes = sell_outcomes(orders, key_stage, key_action)
    trades: dict[tuple[str, str, int], float] = defaultdict(float)
    for o in orders:
        outcome = outcomes.get(o.id)
        if outcome is not None:
            trades[(o.market_id, o.side, key_stage(o.order_key))] += outcome[1]

    rows = {
        stage: {"stage": stage, "wins": 0, "losses": 0, "trades": 0, "win_rate": None, "pnl": 0.0}
        for stage in stages
    }
    for (_, _, stage), pnl in trades.items():
        row = rows.get(stage)
        if row is None:
            continue
        row["wins" if pnl > 0 else "losses"] += 1
        row["trades"] += 1
        row["pnl"] += pnl
    for row in rows.values():
        row["pnl"] = round(row["pnl"], 6)
        row["win_rate"] = round(row["wins"] / row["trades"] * 100.0, 2) if row["trades"] else None
    return [rows[stage] for stage in stages]
