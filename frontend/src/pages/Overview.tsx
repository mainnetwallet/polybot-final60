import type { ReactNode } from "react";
import { api } from "../api/client";
import { ModeBadge, SideBadge } from "../components/Badges";
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

function humanizeReason(reason: string, finalWindowSeconds: number): string {
  switch (reason) {
    case "no_market":
      return "Waiting for the next market";
    case "market_not_started":
      return "Market discovered — observation phase";
    case "outside_final_window":
      return `Outside the configured final ${finalWindowSeconds} seconds; trading is disabled`;
    case "market_expired":
      return "Market expired — no new orders allowed";
    case "in_window":
      return "Final window active — monitoring both asks";
    default:
      return reason.replace(/_/g, " ");
  }
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

      <div className="mt-5 grid grid-cols-2 gap-3 border-t hairline pt-4 sm:grid-cols-4">
        <div>
          <div className="label">Market remaining</div>
          <div className="mono mt-1 text-[14px] text-slate-200">
            {remaining !== null ? `${Math.max(0, Math.ceil(remaining))}s` : "—"}
          </div>
        </div>
        <div>
          <div className="label">Final window</div>
          <div className="mono mt-1 text-[14px] text-slate-200">{finalWindow}s</div>
        </div>
        <div>
          <div className="label">Conflict policy</div>
          <div className="mt-1 text-[12px] capitalize text-slate-300">
            {strategy.conflict_policy.replace(/_/g, " ")}
          </div>
        </div>
        <div>
          <div className="label">Duplicate policy</div>
          <div className="mt-1 text-[12px] capitalize text-slate-300">
            {strategy.duplicate_policy.replace(/_/g, " ")}
          </div>
        </div>
      </div>
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

function StrategyPulse() {
  const { snapshot } = useBot();
  const strategy = snapshot?.strategy;
  const armed = (strategy?.up_armed || strategy?.down_armed) ?? false;
  const phase = !strategy
    ? "WAIT"
    : strategy.in_window
      ? armed
        ? "TRIGGER"
        : "MONITOR"
      : "OBSERVE";

  return (
    <section className="panel p-5 xl:col-span-4">
      <PanelHead
        label="Strategy state"
        title="Configured ask-trigger strategy"
        right={
          <span
            className={cx(
              "chip",
              phase === "TRIGGER"
                ? "border-up/30 bg-up/10 text-up"
                : phase === "MONITOR"
                  ? "border-amber/30 bg-amber/10 text-amber"
                  : "border-white/10 bg-white/5 text-slate-400",
            )}
          >
            {phase}
          </span>
        }
      />

      <div className="space-y-2.5">
        {(["UP", "DOWN"] as const).map((side) => {
          const ask = side === "UP" ? strategy?.up_ask : strategy?.down_ask;
          const isArmed = side === "UP" ? strategy?.up_armed : strategy?.down_armed;
          const threshold = strategy?.trigger_threshold ?? null;
          return (
            <div
              key={side}
              className={cx(
                "flex items-center justify-between rounded-lg border px-3 py-2.5",
                isArmed
                  ? side === "UP"
                    ? "border-up/25 bg-up/[0.06]"
                    : "border-down/25 bg-down/[0.06]"
                  : "border-white/5 bg-black/20",
              )}
            >
              <div className="flex items-center gap-2">
                <SideBadge side={side} size="sm" />
                <span className="mono text-[13px] text-slate-300">{fmtCents(ask)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="mono text-[10px] text-slate-600">≥ {fmtCents(threshold)}</span>
                {isArmed ? (
                  <span className={cx("chip", side === "UP" ? "border-up/30 bg-up/10 text-up" : "border-down/30 bg-down/10 text-down")}>
                    <IconZap size={9} />
                    Armed
                  </span>
                ) : (
                  <span className="chip border-white/8 bg-white/[0.03] text-slate-500">idle</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 rounded-lg border hairline bg-black/20 px-3 py-2.5">
        <div className="label">Engine reason</div>
        <div className="mt-1 text-[12px] leading-relaxed text-slate-300">
          {strategy ? humanizeReason(strategy.reason, strategy.final_window_seconds) : "Connecting to strategy engine"}
        </div>
        {strategy?.remaining_seconds !== null && strategy?.remaining_seconds !== undefined ? (
          <div className="mono mt-1.5 text-[10.5px] text-slate-600">
            T-{fmtNum(Math.max(0, strategy.remaining_seconds), 0)}s · window {strategy.final_window_seconds}s
          </div>
        ) : null}
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
          {strategy?.duplicate_policy.replace(/_/g, " ") ?? "—"}
        </span>
        <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
          {strategy?.conflict_policy.replace(/_/g, " ") ?? "—"}
        </span>
      </div>
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
    () => (mode ? api.orders(mode, 8) : Promise.resolve([])),
    [mode],
    dataVersion,
    mode !== undefined,
  );
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
      <StrategyPulse />
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
          <OrdersTable orders={orders.data} emptyHint="Triggers that qualify create order records." />
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
