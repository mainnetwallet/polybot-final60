import { useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { OrderRow, PositionRow, SellableHolding } from "../api/types";
import { ModeBadge, SideBadge, StageChip } from "../components/Badges";
import { CountdownRing } from "../components/CountdownRing";
import { EmptyState } from "../components/EmptyState";
import {
  IconActivity,
  IconAlert,
  IconClock,
  IconLayers,
  IconTarget,
  IconWifi,
  IconWifiOff,
  IconZap,
} from "../components/Icons";
import { PriceCard } from "../components/PriceCard";
import { PriceToBeatBar } from "../components/PriceToBeatBar";
import { Stat } from "../components/Stat";
import { OrdersTable, PositionsTable } from "../components/Tables";
import { useBot } from "../hooks/BotContext";
import { LoadingPanel } from "../components/LoadingPanel";
import { useRemainingSeconds } from "../hooks/useCountdown";
import { useLiveRefresh } from "../hooks/useLiveRefresh";
import {
  clamp,
  cx,
  fmtCents,
  fmtNum,
  fmtSignedUsd,
  fmtTime,
  fmtUsd,
  pnlClass,
  shortId,
} from "../lib/format";

function PanelHead({
  label,
  title,
  right,
}: {
  label: string;
  title?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div>
        <div className="label">{label}</div>
        {title ? <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">{title}</div> : null}
      </div>
      {right}
    </div>
  );
}

function MarketPanel() {
  const { snapshot } = useBot();
  const remaining = useRemainingSeconds();
  if (!snapshot) return <LoadingPanel className="xl:col-span-8" />;
  const market = snapshot.market;
  const strategy = snapshot.strategy;
  const duration = market?.duration_seconds ?? null;
  const finalWindow = strategy.final_window_seconds;
  const inWindow = remaining !== null && remaining > 0 && remaining <= finalWindow;
  const phaseLabel = inWindow ? "Final window" : remaining !== null ? "Observation" : "—";
  const elapsed = duration !== null && remaining !== null ? clamp(duration - remaining, 0, duration) : null;
  const progress = duration && elapsed !== null ? (elapsed / duration) * 100 : null;
  const finalStart = duration ? ((duration - finalWindow) / duration) * 100 : null;
  const durationLabel = duration == null
    ? "—"
    : duration % 60 === 0
      ? `${duration / 60}-minute window`
      : `${duration}-second window`;

  return (
    <section className="panel relative overflow-hidden p-5 xl:col-span-8">
      <div className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-up/[0.05] blur-3xl" />
      <PanelHead
        label="Active market"
        title={market ? `Bitcoin · Up or Down · ${durationLabel}` : "Bitcoin · Up or Down"}
        right={
          market ? (
            <span className="chip border-white/8 bg-white/[0.03] font-mono text-slate-400">
              {shortId(market.event_slug, 18, 8)}
            </span>
          ) : null
        }
      />

      {market ? (
        <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={cx(
                  "chip",
                  inWindow ? "border-amber/30 bg-amber/10 text-amber" : "border-sky/25 bg-sky/10 text-sky",
                )}
              >
                <IconClock size={10} />
                {phaseLabel}
              </span>
              <span className="chip border-white/8 bg-white/[0.03] text-slate-400">
                <IconTarget size={10} />
                Trigger {fmtCents(strategy.trigger_threshold)}
              </span>
              <span className="chip border-white/8 bg-white/[0.03] text-slate-400">
                <IconZap size={10} />
                Marketable buy
              </span>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3 text-[12px]">
              <div>
                <div className="label">Window start</div>
                <div className="mono mt-1 text-slate-300">{fmtTime(market.window_start)}</div>
              </div>
              <div>
                <div className="label">Expiry</div>
                <div className="mono mt-1 text-slate-300">{fmtTime(market.window_end)}</div>
              </div>
              <div>
                <div className="label">UP asset</div>
                <div className="mono mt-1 text-up/80" title={market.up_token_id}>
                  {shortId(market.up_token_id, 10, 4)}
                </div>
              </div>
              <div>
                <div className="label">DOWN asset</div>
                <div className="mono mt-1 text-down/80" title={market.down_token_id}>
                  {shortId(market.down_token_id, 10, 4)}
                </div>
              </div>
            </div>

            <div className="mt-5">
              <div className="relative h-2 overflow-hidden rounded-full bg-white/[0.05]">
                {finalStart !== null ? <div className="absolute inset-y-0 right-0 bg-amber/[0.10]" style={{ width: `${100 - finalStart}%` }} /> : null}
                {progress !== null ? (
                  <div
                    className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-sky/60 to-sky"
                    style={{ width: `${progress}%` }}
                  />
                ) : null}
                {finalStart !== null ? <div className="absolute inset-y-0 w-px bg-amber/70" style={{ left: `${finalStart}%` }} /> : null}
              </div>
              <div className="mt-2 flex items-center justify-between text-[10px] text-slate-600">
                <span className="mono">0:00 · open</span>
                <span className="mono text-amber/80">
                  {duration !== null ? Math.round(duration - finalWindow) : "—"}s · final window
                </span>
                <span className="mono">{duration ? `${Math.round(duration / 60)}:00 · expiry` : "expiry"}</span>
              </div>
            </div>
          </div>

          <CountdownRing
            remaining={remaining}
            inWindow={inWindow}
            finalWindow={finalWindow}
            duration={duration}
            armed={strategy.up_armed || strategy.down_armed}
            stage0Enabled={strategy.stage0_enabled}
            stage0Start={strategy.stage0_start_seconds}
            stage0End={strategy.stage0_end_seconds}
            stage2Seconds={strategy.stage2_seconds}
            stage3Seconds={strategy.stage3_seconds}
          />
        </div>
      ) : (
        <EmptyState
          icon={<IconClock size={26} />}
          title="No active market"
          hint="The next market is picked up automatically when it becomes available."
        />
      )}
    </section>
  );
}

function AccountPanel() {
  const { snapshot } = useBot();
  if (!snapshot) return <LoadingPanel className="xl:col-span-4" />;
  const account = snapshot.account;
  const config = snapshot.config;
  const daily = account.daily_pnl;
  const loss = typeof config.max_daily_loss === "number" ? config.max_daily_loss : null;
  const target = typeof config.daily_profit_target === "number" ? config.daily_profit_target : null;

  const span = loss !== null && target !== null ? loss + target : null;
  const fill = span !== null && span > 0 ? clamp(((daily + loss!) / span) * 100, 0, 100) : null;
  const zero = span !== null && span > 0 ? (loss! / span) * 100 : null;

  return (
    <section className="panel p-5 xl:col-span-4">
      <PanelHead
        label="Account"
        title="Daily risk envelope"
        right={<ModeBadge mode={snapshot.bot.mode} />}
      />

      <div className="flex items-end justify-between">
        <div>
          <div className="label">Available balance</div>
          <div className="mono mt-1 text-[30px] font-semibold leading-none tracking-tight text-slate-100">
            {fmtUsd(account.balance)}
          </div>
        </div>
        <div className="text-right">
          <div className="label">Today</div>
          <div className={cx("mono mt-1 text-[18px] font-semibold leading-none", pnlClass(daily))}>
            {fmtSignedUsd(daily)}
          </div>
        </div>
      </div>

      <div className="mt-5">
        <div className="relative h-2 overflow-hidden rounded-full bg-white/[0.05]">
          {zero !== null ? <div className="absolute inset-y-0 left-0 bg-down/20" style={{ width: `${zero}%` }} /> : null}
          {zero !== null ? <div className="absolute inset-y-0 right-0 bg-up/15" style={{ width: `${100 - zero}%` }} /> : null}
          {zero !== null ? <div className="absolute inset-y-0 w-px bg-white/40" style={{ left: `${zero}%` }} /> : null}
          {fill !== null ? (
            <div
              className={cx(
                "absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink-900",
                daily < 0 ? "bg-down" : "bg-up",
              )}
              style={{ left: `${fill}%` }}
            />
          ) : null}
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px]">
          <span className="mono text-down/80">stop -{loss !== null ? fmtUsd(loss) : "—"}</span>
          <span className="mono text-slate-600">{fmtUsd(account.starting_balance)} start</span>
          <span className="mono text-up/80">target +{target !== null ? fmtUsd(target) : "—"}</span>
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-x-4 gap-y-4 border-t hairline pt-4">
        <Stat label="Realized" value={fmtSignedUsd(account.realized_pnl)} tone={account.realized_pnl >= 0 ? "up" : "down"} />
        <Stat
          label="Unrealized"
          value={fmtSignedUsd(account.unrealized_pnl)}
          tone={account.unrealized_pnl >= 0 ? "up" : "down"}
          hint={
            account.positions_awaiting_settlement
              ? `${account.positions_awaiting_settlement} settling…`
              : account.pending_redeems
                ? `${account.pending_redeems} redeeming…`
                : undefined
          }
        />
        <Stat label="Trades today" value={account.trades_today} hint={`max ${String(config.max_trades_per_day ?? "—")} / day`} />
        <Stat
          label="Open positions"
          value={snapshot.positions.length}
          hint={`max ${String(config.max_open_positions ?? "—")}`}
        />
      </div>
    </section>
  );
}

type StageHolding = {
  key: string;
  side: "UP" | "DOWN";
  stage: number | null;
  size: number;
  entry: number | null;
  bid: number | null;
  pnl: number | null;
  holding: SellableHolding | null;
};

/** Volume-weighted entry price of the filled BUY orders of one stage of one market side. */
function stageEntryPrice(orders: OrderRow[], marketId: string, side: string, stage: number): number | null {
  let cost = 0;
  let qty = 0;
  for (const o of orders) {
    if (o.market_id !== marketId || o.side !== side || o.action !== "BUY" || !(o.filled_size > 0)) continue;
    if ((o.stage ?? 0) !== stage) continue;
    const price = o.avg_fill_price ?? o.submitted_price ?? o.requested_price;
    cost += price * o.filled_size;
    qty += o.filled_size;
  }
  return qty > 0 ? cost / qty : null;
}

/** Everything held in the live market, one row per stage (a stage held on its own stays its own row), each with a Sell button. */
function PositionPanel({ orders }: { orders: OrderRow[] }) {
  const { snapshot, sellNow, sellAll } = useBot();
  const [selling, setSelling] = useState<Set<string>>(new Set());
  if (!snapshot) return <LoadingPanel className="xl:col-span-4" />;

  const marketId = snapshot.market?.market_id ?? null;
  const sellable = snapshot.sellable ?? [];
  const rows: StageHolding[] = [];
  const open: PositionRow[] = marketId
    ? snapshot.positions.filter((p) => p.market_id === marketId && p.status === "open" && p.size > 0)
    : [];
  for (const p of open) {
    const bid = (p.side === "UP" ? snapshot.books.up : snapshot.books.down)?.best_bid ?? null;
    const stages = sellable.filter((h) => h.market_id === p.market_id && h.side === p.side);
    if (stages.length === 0) {
      // held, but nothing sellable right now (sell in flight, kill switch, ...): show the whole position
      rows.push({
        key: `${p.market_id}:${p.side}`,
        side: p.side,
        stage: null,
        size: p.size,
        entry: p.avg_price,
        bid,
        pnl: p.unrealized_pnl,
        holding: null,
      });
      continue;
    }
    for (const h of stages) {
      const entry = stageEntryPrice(orders, p.market_id, p.side, h.stage);
      rows.push({
        key: `${h.market_id}:${h.side}:${h.stage}`,
        side: p.side,
        stage: h.stage,
        size: h.size,
        entry,
        bid,
        pnl: entry !== null && bid !== null ? (bid - entry) * h.size : null,
        holding: h,
      });
    }
  }
  rows.sort((x, y) => (x.stage ?? 99) - (y.stage ?? 99) || x.side.localeCompare(y.side));

  const sell = (row: StageHolding) => {
    if (!row.holding || selling.has(row.key)) return; // only blocks a double click while the request is on its way
    setSelling((prev) => new Set(prev).add(row.key));
    void Promise.resolve(sellNow(row.holding)).finally(() =>
      setSelling((prev) => {
        const next = new Set(prev);
        next.delete(row.key);
        return next;
      }),
    );
  };

  const sellableRows = rows.filter((row) => row.holding);
  const sellingAll = selling.has("__all__");
  const sellEverything = () => {
    if (!marketId || sellableRows.length === 0 || sellingAll) return; // only blocks a double click while the request is on its way
    setSelling((prev) => new Set(prev).add("__all__"));
    void Promise.resolve(sellAll(marketId)).finally(() =>
      setSelling((prev) => {
        const next = new Set(prev);
        next.delete("__all__");
        return next;
      }),
    );
  };

  return (
    <section className="panel p-5 xl:col-span-4">
      <PanelHead
        label="Position"
        title="Held in this market"
        right={
          <div className="flex items-center gap-2">
            {sellableRows.length > 0 ? (
              <button
                type="button"
                onClick={sellEverything}
                disabled={sellingAll}
                title={`Sell every stage that holds shares (${sellableRows.length}) now at the market`}
                className="rounded-md border border-rose-400/40 bg-rose-400/15 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-rose-300 transition hover:bg-rose-400/25 disabled:cursor-wait disabled:opacity-50"
              >
                {sellingAll ? "Selling…" : "Sell all"}
              </button>
            ) : null}
            <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
              <IconLayers size={10} />
              {rows.length}
            </span>
          </div>
        }
      />

      {rows.length === 0 ? (
        <EmptyState
          icon={<IconLayers size={24} />}
          title="No open position"
          hint="Filled buys of the live market appear here, one row per stage."
        />
      ) : (
        <div className="scrollbar max-h-[22rem] space-y-2.5 overflow-y-auto pr-1">
          {rows.map((row) => (
            <div
              key={row.key}
              className={cx(
                "rounded-lg border px-3 py-2.5",
                row.side === "UP" ? "border-up/20 bg-up/[0.04]" : "border-down/20 bg-down/[0.04]",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <StageChip stage={row.stage} />
                  <SideBadge side={row.side} size="sm" />
                  <span className="mono text-[12px] text-slate-300">{fmtNum(row.size, 2)} sh</span>
                </div>
                {row.holding ? (
                  <button
                    type="button"
                    onClick={() => sell(row)}
                    disabled={selling.has(row.key)}
                    title={`Sell ${fmtNum(row.holding.size, 2)} shares now at the market`}
                    className="rounded-md border border-rose-400/40 bg-rose-400/15 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-rose-300 transition hover:bg-rose-400/25 disabled:cursor-wait disabled:opacity-50"
                  >
                    {selling.has(row.key) ? "Selling…" : "Sell"}
                  </button>
                ) : null}
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
                <div>
                  <div className="label">Entry</div>
                  <div className="mono mt-0.5 text-slate-300">{fmtCents(row.entry)}</div>
                </div>
                <div>
                  <div className="label">Bid</div>
                  <div className="mono mt-0.5 text-slate-300">{fmtCents(row.bid)}</div>
                </div>
                <div>
                  <div className="label">PnL</div>
                  <div className={cx("mono mt-0.5", row.pnl === null ? "text-slate-600" : pnlClass(row.pnl))}>
                    {row.pnl === null ? "—" : fmtSignedUsd(row.pnl)}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function FeedPanel() {
  const { snapshot, clockOffsetMs } = useBot();
  const data = snapshot?.market_data;
  const config = snapshot?.config ?? {};
  const lastMessage = data?.last_message_ms;
  const age = lastMessage ? Date.now() + clockOffsetMs - lastMessage : null;

  return (
    <section className="panel p-5 xl:col-span-4">
      <PanelHead
        label="Infrastructure"
        title="Feed & execution"
        right={
          <span
            className={cx(
              "chip",
              data === undefined
                ? "border-white/8 bg-white/[0.03] text-slate-500"
                : data.connected
                  ? "border-up/30 bg-up/10 text-up"
                  : "border-down/30 bg-down/10 text-down",
            )}
          >
            {data === undefined ? null : data.connected ? <IconWifi size={10} /> : <IconWifiOff size={10} />}
            {data === undefined ? "Loading" : data.connected ? "Connected" : "Disconnected"}
          </span>
        }
      />

      <div className="grid grid-cols-2 gap-x-4 gap-y-4">
        <Stat
          label="Last message"
          value={age === null ? "—" : age < 1000 ? "now" : `${Math.round(age / 1000)}s ago`}
          hint="last book event"
        />
        <Stat label="Reconnects" value={data?.reconnects ?? "—"} hint="websocket feed" />
        <Stat label="Order size" value={fmtUsd(typeof config.trade_amount_usdc === "number" ? config.trade_amount_usdc : null)} hint="marketable notional" />
        <Stat
          label="Max exec price"
          value={fmtCents(typeof config.max_execution_price === "number" ? config.max_execution_price : null)}
          hint="hard price cap"
        />
      </div>

      {data?.last_error ? (
        <div className="mt-4 flex gap-2 rounded-lg border border-down/20 bg-down/[0.05] px-3 py-2 text-[11px] text-down/90">
          <IconAlert size={13} className="mt-0.5 shrink-0" />
          <span className="break-all">{data.last_error}</span>
        </div>
      ) : (
        <div className="mt-4 flex items-center gap-2 rounded-lg border hairline bg-black/20 px-3 py-2 text-[11px] text-slate-500">
          <IconActivity size={13} className="text-up/70" />
          Event-driven pipeline: WS → strategy → risk → order
        </div>
      )}
    </section>
  );
}

export function Overview() {
  const { snapshot, history, dataVersion } = useBot();
  const mode = snapshot?.bot.mode;
  const orders = useLiveRefresh(
    () => (mode ? api.orders(mode, 40) : Promise.resolve([])),
    [mode],
    dataVersion,
    mode !== undefined,
  );
  const visibleOrders = (orders.data ?? []).slice(0, 8);
  const config = snapshot?.config ?? {};
  const stale = typeof config.stale_data_max_age_ms === "number" ? config.stale_data_max_age_ms : null;

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <PriceToBeatBar className="xl:col-span-12" />
      <MarketPanel />
      <AccountPanel />

      <PriceCard
        side="UP"
        book={snapshot?.books.up ?? null}
        armed={snapshot?.strategy.up_armed ?? false}
        threshold={snapshot?.strategy.trigger_threshold ?? null}
        history={history.up}
        inWindow={snapshot?.strategy.in_window ?? false}
        staleAfterMs={stale}
        className="xl:col-span-4"
      />
      <PriceCard
        side="DOWN"
        book={snapshot?.books.down ?? null}
        armed={snapshot?.strategy.down_armed ?? false}
        threshold={snapshot?.strategy.trigger_threshold ?? null}
        history={history.down}
        inWindow={snapshot?.strategy.in_window ?? false}
        staleAfterMs={stale}
        className="xl:col-span-4"
      />
      <PositionPanel orders={orders.data ?? []} />
      <FeedPanel />

      <section className="panel overflow-hidden xl:col-span-8">
        <div className="flex items-center justify-between border-b hairline px-5 py-4">
          <div>
            <div className="label">Execution</div>
            <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Recent orders</div>
          </div>
          <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
            {snapshot ? `${snapshot.open_orders.length} open · ${mode ?? "—"}` : "—"}
          </span>
        </div>
        {orders.data ? (
          <OrdersTable
            orders={visibleOrders}
            emptyHint="Triggers that qualify create order records."
          />
        ) : (
          <LoadingPanel className="m-4" />
        )}
      </section>

      <section className="panel overflow-hidden xl:col-span-12">
        <div className="flex items-center justify-between border-b hairline px-5 py-4">
          <div>
            <div className="label">Portfolio</div>
            <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Open positions</div>
          </div>
          <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
            <IconLayers size={10} />
            {snapshot?.positions.length ?? "—"}
          </span>
        </div>
        {snapshot ? <PositionsTable positions={snapshot.positions} /> : <LoadingPanel className="m-4" />}
      </section>
    </div>
  );
}
