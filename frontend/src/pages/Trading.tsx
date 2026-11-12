import { api } from "../api/client";
import { ModeBadge, SideBadge, StatusChip } from "../components/Badges";
import { EmptyState } from "../components/EmptyState";
import { IconActivity, IconLayers, IconZap } from "../components/Icons";
import { Stat } from "../components/Stat";
import { OrdersTable, PositionsTable } from "../components/Tables";
import { useBot } from "../hooks/BotContext";
import { usePolled } from "../hooks/usePolled";
import {
  cx,
  fmtCents,
  fmtNum,
  fmtSignedUsd,
  fmtTime,
  fmtUsd,
  pnlClass,
  shortId,
} from "../lib/format";

export function Trading() {
  const { snapshot, dataVersion } = useBot();
  const mode = snapshot?.bot.mode ?? "paper";
  const polled = usePolled(async () => {
    const [orders, positions, analytics] = await Promise.all([
      api.orders(mode, 100),
      api.positions(mode, 100),
      api.analytics(mode),
    ]);
    return { orders, positions, analytics };
  }, 5000, [mode], dataVersion);

  const analytics = polled.data?.analytics;
  const last = snapshot?.last_order;
  const openOrders = snapshot?.open_orders ?? [];

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <section className="panel flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between xl:col-span-12">
        <div className="flex items-center gap-3">
          {snapshot ? <ModeBadge mode={snapshot.bot.mode} /> : null}
          <div>
            <div className="text-[13px] font-semibold text-slate-100">
              {mode === "live" ? "Live CLOB execution" : "Paper execution on live market data"}
            </div>
            <div className="mt-0.5 text-[11px] text-slate-500">
              {mode === "live"
                ? "Real FOK marketable orders; balances and positions reconciled with the exchange."
                : "Simulated fills walk the live ask ladder with slippage, fees and partial fills."}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div>
            <div className="label">Balance</div>
            <div className="mono mt-1 text-[16px] text-slate-100">{fmtUsd(snapshot?.account.balance)}</div>
          </div>
          <div>
            <div className="label">Daily PnL</div>
            <div className={cx("mono mt-1 text-[16px]", pnlClass(snapshot?.account.daily_pnl))}>
              {fmtSignedUsd(snapshot?.account.daily_pnl)}
            </div>
          </div>
          <div>
            <div className="label">Open orders</div>
            <div className="mono mt-1 text-[16px] text-slate-100">{openOrders.length}</div>
          </div>
        </div>
      </section>

      <section className="panel p-5 xl:col-span-12">
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="Total trades" value={analytics?.total_trades ?? 0} hint={`${analytics?.closed_trades ?? 0} settled`} />
          <Stat
            label="Win rate"
            value={analytics?.win_rate != null ? `${analytics.win_rate.toFixed(1)}%` : "—"}
            hint={`${analytics?.wins ?? 0}W / ${analytics?.losses ?? 0}L`}
            tone={(analytics?.win_rate ?? 0) >= 50 ? "up" : "default"}
          />
          <Stat
            label="Total PnL"
            value={fmtSignedUsd(analytics?.total_pnl)}
            tone={(analytics?.total_pnl ?? 0) >= 0 ? "up" : "down"}
            hint={`fees ${fmtUsd(analytics?.fees_paid ?? 0, 3)}`}
          />
          <Stat label="Volume" value={fmtUsd(analytics?.volume_usdc ?? 0)} hint="USDC notional" />
          <Stat label="Avg entry" value={fmtCents(analytics?.avg_entry_price)} hint="share price" />
        </div>
      </section>

      <section className="panel p-5 xl:col-span-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="label">Most recent execution</div>
            <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Last order</div>
          </div>
          {last ? <SideBadge side={last.side} size="sm" /> : null}
        </div>
        {last ? (
          <div className="mt-4 space-y-3">
            <div className="flex items-center justify-between">
              <StatusChip status={last.status} />
              <span className="mono text-[11px] text-slate-500">{last.ts ? fmtTime(last.ts) : "—"}</span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="label">Filled</div>
                <div className="mono mt-1 text-[13px] text-slate-200">{fmtNum(last.filled_size, 2)} sh</div>
              </div>
              <div>
                <div className="label">Avg price</div>
                <div className="mono mt-1 text-[13px] text-slate-200">{fmtCents(last.avg_price)}</div>
              </div>
              <div>
                <div className="label">Order id</div>
                <div className="mono mt-1 text-[13px] text-slate-200">{last.order_id ?? "—"}</div>
              </div>
            </div>
            <div className="rounded-lg border hairline bg-black/20 px-3 py-2">
              <div className="label">Reason</div>
              <div className="mt-1 text-[11.5px] text-slate-400">{last.reason || "—"}</div>
              {last.exchange_order_id ? (
                <div className="mono mt-1 truncate text-[10px] text-slate-600" title={last.exchange_order_id}>
                  exchange {shortId(last.exchange_order_id, 14, 6)}
                </div>
              ) : null}
              {last.error ? <div className="mt-1 text-[10.5px] text-down">{last.error}</div> : null}
            </div>
          </div>
        ) : (
          <EmptyState
            icon={<IconZap size={26} />}
            title="No executions yet"
            hint="Start the bot and wait for an ask to reach the trigger inside the final window."
          />
        )}
      </section>

      <section className="panel overflow-hidden xl:col-span-8">
        <div className="flex items-center justify-between border-b hairline px-5 py-4">
          <div>
            <div className="label">Working now</div>
            <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Open orders</div>
          </div>
          <span className="chip border-white/8 bg-white/[0.03] text-slate-500">{openOrders.length} outstanding</span>
        </div>
        <OrdersTable orders={openOrders} emptyHint="Nothing outstanding. New orders are inserted as PENDING and resolved by the executor." />
      </section>

      <section className="panel overflow-hidden xl:col-span-12">
        <div className="flex items-center justify-between border-b hairline px-5 py-4">
          <div>
            <div className="label">{mode} ledger</div>
            <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Order history</div>
          </div>
          <div className="flex items-center gap-2">
            {polled.error ? <span className="chip border-down/25 bg-down/10 text-down">api error</span> : null}
            <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
              <IconLayers size={10} />
              {polled.data?.orders.length ?? 0} orders
            </span>
          </div>
        </div>
        <OrdersTable orders={polled.data?.orders ?? []} detailed />
      </section>

      <section className="panel overflow-hidden xl:col-span-12">
        <div className="flex items-center justify-between border-b hairline px-5 py-4">
          <div>
            <div className="label">{mode} portfolio</div>
            <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Open positions</div>
          </div>
          <div className="flex items-center gap-2">
            <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
              <IconActivity size={10} />
              {polled.data?.positions.length ?? 0} positions
            </span>
          </div>
        </div>
        <PositionsTable positions={polled.data?.positions ?? []} detailed />
      </section>
    </div>
  );
}
