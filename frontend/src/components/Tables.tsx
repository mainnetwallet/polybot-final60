import type { OrderRow, PositionRow } from "../api/types";
import { cx, fmtCents, fmtDateTime, fmtNum, fmtSignedUsd, fmtTime, fmtUsd, pnlClass, shortId } from "../lib/format";
import { SideBadge, StatusChip } from "./Badges";
import { EmptyState } from "./EmptyState";
import { IconLayers } from "./Icons";

export function OrdersTable({
  orders,
  detailed = false,
  emptyHint,
}: {
  orders: OrderRow[];
  detailed?: boolean;
  emptyHint?: string;
}) {
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
            <th>Status</th>
            <th className="text-right">Notional</th>
            <th className="text-right">Price</th>
            <th className="text-right">Filled</th>
            <th className="text-right">Fee</th>
            {detailed ? <th>Exchange ID</th> : null}
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => {
            const price = order.avg_fill_price ?? order.submitted_price ?? order.requested_price;
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
                  <StatusChip status={order.status} />
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
