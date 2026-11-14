import { useState } from "react";
import type { OrderRow, PositionRow, SellableHolding, StageResults } from "../api/types";
import { cx, fmtCents, fmtDateTime, fmtNum, fmtSignedUsd, fmtTime, fmtUsd, pnlClass, shortId } from "../lib/format";
import { ResultChip, SideBadge, StageChip, StatusChip } from "./Badges";
import { EmptyState } from "./EmptyState";
import { IconLayers } from "./Icons";

/** Running WIN / LOSS count of one stage (all its finished exits), updated live. */
function StageTally({ row, stage }: { row?: StageResults["stages"][number]; stage: number }) {
  if (!row || row.trades === 0) return null;
  return (
    <div
      className="mono mt-0.5 text-[10px] text-slate-500"
      title={`Stage ${stage}: ${row.wins} wins, ${row.losses} losses, total P&L ${row.pnl >= 0 ? "+" : "-"}$${Math.abs(row.pnl).toFixed(2)}`}
    >
      S{stage} <span className="text-up">{row.wins}W</span>
      <span className="text-slate-600"> / </span>
      <span className="text-down">{row.losses}L</span>
    </div>
  );
}

export function OrdersTable({
  orders,
  detailed = false,
  emptyHint,
  sellable,
  onSell,
  stageResults,
}: {
  orders: OrderRow[];
  detailed?: boolean;
  emptyHint?: string;
  /** shares that can be sold right now; with `onSell` this adds a "Sell" button to their BUY row */
  sellable?: SellableHolding[];
  /** called the moment the button is clicked: there is no confirmation step */
  onSell?: (holding: SellableHolding) => Promise<unknown>;
  /** live WIN / LOSS count per stage; shown in the Result column */
  stageResults?: StageResults | null;
}) {
  const [selling, setSelling] = useState<Set<string>>(new Set());
  const showActions = onSell !== undefined;
  // One button per stage: it sits on the newest filled BUY row of that stage (rows arrive newest first).
  const sellRows = new Map<number, SellableHolding>();
  if (showActions && sellable?.length) {
    const taken = new Set<string>();
    for (const order of orders) {
      if (order.action !== "BUY" || !(order.filled_size > 0)) continue;
      const holding = sellable.find(
        (h) => h.market_id === order.market_id && h.side === order.side && h.stage === (order.stage ?? 0),
      );
      if (!holding) continue;
      const key = `${holding.market_id}:${holding.side}:${holding.stage}`;
      if (taken.has(key)) continue;
      taken.add(key);
      sellRows.set(order.id, holding);
    }
  }
  const sell = (holding: SellableHolding) => {
    const key = `${holding.market_id}:${holding.side}:${holding.stage}`;
    if (selling.has(key)) return; // only blocks a double click while this request is on its way
    setSelling((prev) => new Set(prev).add(key));
    void Promise.resolve(onSell?.(holding)).finally(() =>
      setSelling((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      }),
    );
  };

  if (!orders.length) {
    return (
      <EmptyState
        icon={<IconLayers size={26} />}
        title="No orders yet"
        hint={emptyHint ?? "Orders appear here when a configured trigger qualifies and execution begins."}
      />
    );
  }

  return (
    <div className="overflow-x-auto scrollbar">
      <table className="data-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Market</th>
            <th>Side</th>
            <th>Stage</th>
            <th>Status</th>
            <th>
              Result
              {stageResults && stageResults.total.trades > 0 ? (
                <span className="mono ml-2 text-[10px] font-normal normal-case tracking-normal">
                  <span className="text-up">{stageResults.total.wins}W</span>
                  <span className="text-slate-600"> / </span>
                  <span className="text-down">{stageResults.total.losses}L</span>
                </span>
              ) : null}
            </th>
            <th className="text-right">Notional</th>
            <th className="text-right">Price</th>
            <th className="text-right">Filled</th>
            <th className="text-right">Fee</th>
            {detailed ? <th>Exchange ID</th> : null}
            {showActions ? <th className="text-right">Action</th> : null}
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => {
            const price = order.avg_fill_price ?? order.submitted_price ?? order.requested_price;
            const holding = sellRows.get(order.id);
            const holdingKey = holding ? `${holding.market_id}:${holding.side}:${holding.stage}` : "";
            return (
              <tr key={order.id}>
                <td className="mono text-slate-500">{fmtTime(order.created_at)}</td>
                <td className="mono text-slate-500" title={order.market_id}>
                  {shortId(order.market_id, 12, 4)}
                </td>
                <td>
                  <SideBadge side={order.side} size="sm" />
                </td>
                <td>
                  <StageChip stage={order.stage} action={order.action} />
                </td>
                <td>
                  <StatusChip status={order.status} action={order.action} reason={order.reason} />
                </td>
                <td>
                  <ResultChip result={order.action === "BUY" && order.filled_size > 0 ? "BUY" : order.result} pnl={order.result_pnl} />
                  {order.action === "SELL" && order.result && stageResults ? (
                    <StageTally row={stageResults.stages.find((s) => s.stage === (order.stage ?? 0))} stage={order.stage ?? 0} />
                  ) : null}
                </td>
                <td className="mono text-right text-slate-300">{fmtUsd(order.requested_notional)}</td>
                <td className="mono text-right text-slate-300">{fmtCents(price)}</td>
                <td className="mono text-right text-slate-300">{fmtNum(order.filled_size, 2)}</td>
                <td className="mono text-right text-slate-500">{fmtUsd(order.fee, 3)}</td>
                {detailed ? (
                  <td className="mono text-slate-600" title={order.exchange_order_id ?? order.error ?? ""}>
                    {order.exchange_order_id ? shortId(order.exchange_order_id) : order.error ? "error" : "—"}
                  </td>
                ) : null}
                {showActions ? (
                  <td className="text-right">
                    {holding ? (
                      <button
                        type="button"
                        onClick={() => sell(holding)}
                        disabled={selling.has(holdingKey)}
                        title={`Sell ${fmtNum(holding.size, 2)} shares now at the market`}
                        className="rounded-md border border-rose-400/40 bg-rose-400/15 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-rose-300 transition hover:bg-rose-400/25 disabled:cursor-wait disabled:opacity-50"
                      >
                        {selling.has(holdingKey) ? "Selling…" : "Sell"}
                      </button>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function PositionsTable({
  positions,
  detailed = false,
  emptyHint,
}: {
  positions: PositionRow[];
  detailed?: boolean;
  emptyHint?: string;
}) {
  if (!positions.length) {
    return (
      <EmptyState
        icon={<IconLayers size={26} />}
        title="No open positions"
        hint={emptyHint ?? "Filled buys create positions here; settled markets are written to history."}
      />
    );
  }

  return (
    <div className="overflow-x-auto scrollbar">
      <table className="data-table">
        <thead>
          <tr>
            <th>Market</th>
            <th>Side</th>
            <th className="text-right">Size</th>
            <th className="text-right">Avg price</th>
            <th className="text-right">Cost</th>
            <th className="text-right">Fees</th>
            <th className="text-right">Unrealized</th>
            <th className="text-right">Realized</th>
            {detailed ? <th>Opened</th> : <th>Status</th>}
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={position.id}>
              <td className="mono text-slate-500" title={position.market_id}>
                {shortId(position.market_id, 12, 4)}
              </td>
              <td>
                <SideBadge side={position.side} size="sm" />
              </td>
              <td className="mono text-right text-slate-300">{fmtNum(position.size, 2)}</td>
              <td className="mono text-right text-slate-300">{fmtCents(position.avg_price)}</td>
              <td className="mono text-right text-slate-300">{fmtUsd(position.cost_basis)}</td>
              <td className="mono text-right text-slate-500">{fmtUsd(position.fees, 3)}</td>
              <td className={cx("mono text-right", pnlClass(position.unrealized_pnl))}>
                {position.awaiting_settlement ? (
                  <span title="Window ended. Waiting for Polymarket to publish the winner; balance updates on settlement.">
                    settling…
                  </span>
                ) : (
                  fmtSignedUsd(position.unrealized_pnl)
                )}
              </td>
              <td className={cx("mono text-right", pnlClass(position.realized_pnl))}>
                {fmtSignedUsd(position.realized_pnl)}
              </td>
              {detailed ? (
                <td className="mono text-slate-600">{fmtDateTime(position.opened_at)}</td>
              ) : (
                <td>
                  <StatusChip
                    status={position.awaiting_settlement ? "settling" : position.status}
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
