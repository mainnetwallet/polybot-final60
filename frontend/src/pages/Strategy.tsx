import { api } from "../api/client";
import { SideBadge, StatusChip } from "../components/Badges";
import { EmptyState } from "../components/EmptyState";
import { IconActivity, IconClock, IconShield, IconTarget, IconZap } from "../components/Icons";
import { useBot } from "../hooks/BotContext";
import { LoadingPanel } from "../components/LoadingPanel";
import { useLiveRefresh } from "../hooks/useLiveRefresh";
import { clamp, cx, fmtCents, fmtNum, fmtTime, fmtUsd, shortId } from "../lib/format";

function mmss(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

function PhaseTimeline() {
  const { snapshot } = useBot();
  if (!snapshot) return <LoadingPanel className="xl:col-span-12" />;
  const finalWindow = snapshot.strategy.final_window_seconds;
  const duration = snapshot.market?.duration_seconds ?? null;
  const observeSeconds = duration === null ? null : Math.max(0, duration - finalWindow);
  const observePct = duration && observeSeconds !== null ? (observeSeconds / duration) * 100 : null;
  const active = snapshot.strategy.in_window;

  return (
    <section className="panel p-5 xl:col-span-7">
      <div className="flex items-start justify-between">
        <div>
          <div className="label">Trading phases</div>
          <div className="mt-1 text-[15px] font-semibold tracking-tight text-slate-100">
            {duration === null || observeSeconds === null
              ? "Market timing unavailable"
              : `${observeSeconds % 60 === 0 ? `${observeSeconds / 60} minutes` : `${observeSeconds} seconds`} observation · ${finalWindow} seconds active`}
          </div>
        </div>
        <span className={cx("chip", active ? "border-amber/30 bg-amber/10 text-amber" : "border-sky/25 bg-sky/10 text-sky")}>
          <IconClock size={10} />
          {active ? "In final window" : "Observation"}
        </span>
      </div>

      {observePct !== null && observeSeconds !== null && duration !== null ? <div className="mt-6">
        <div className="flex h-9 overflow-hidden rounded-lg border hairline">
          <div
            className="relative flex items-center justify-center bg-sky/[0.07]"
            style={{ width: `${observePct}%` }}
          >
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-sky/70">
              Observe · no orders
            </span>
          </div>
          <div className="relative flex flex-1 items-center justify-center bg-amber/[0.12]">
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-amber">
              Trade · {finalWindow}s
            </span>
          </div>
        </div>
        <div className="mt-2 flex justify-between text-[10px] text-slate-600">
          <span className="mono">00:00 open</span>
          <span className="mono text-amber/70">{mmss(observeSeconds)} active window</span>
          <span className="mono">{mmss(duration)} expiry</span>
        </div>
      </div> : <div className="mt-6 flex h-9 items-center justify-center rounded-lg border hairline text-[11px] text-slate-500">Waiting for active market timing</div>}

      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border hairline bg-black/20 p-3.5">
          <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-sky/80">
            <span className="flex h-5 w-5 items-center justify-center rounded-md bg-sky/10 text-[10px]">01</span>
            Observation
          </div>
          <p className="mt-2 text-[11.5px] leading-relaxed text-slate-400">
            The engine streams both token books but never constructs an order. No candle, RSI or AI confirmation is
            involved.
          </p>
        </div>
        <div className="rounded-lg border border-amber/20 bg-amber/[0.04] p-3.5">
          <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-amber">
            <span className="flex h-5 w-5 items-center justify-center rounded-md bg-amber/10 text-[10px]">02</span>
            Final {finalWindow}s
          </div>
          <p className="mt-2 text-[11.5px] leading-relaxed text-slate-400">
            Each WebSocket update re-evaluates both best asks. The first ask at or above the trigger fires an
            immediate marketable BUY at the current ask size ladder.
          </p>
        </div>
      </div>

      <div className="mt-5 space-y-2 border-t hairline pt-4">
        {[
          `Threshold $${snapshot.strategy.trigger_threshold.toFixed(2)} is a trigger, never a limit price.`,
          "Order price is the live best ask, bounded by max execution price.",
          "Stale quotes, expired markets and thin liquidity are blocked before submission.",
          "Duplicate and conflict policies decide when both sides qualify.",
        ].map((rule) => (
          <div key={rule} className="flex items-start gap-2 text-[11.5px] text-slate-400">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-up/70" />
            {rule}
          </div>
        ))}
      </div>
    </section>
  );
}

function ArmMeter({
  side,
  ask,
  threshold,
  armed,
}: {
  side: "UP" | "DOWN";
  ask: number | null | undefined;
  threshold: number | null;
  armed: boolean | undefined;
}) {
  const pct = ask == null ? null : clamp(ask, 0, 1) * 100;
  const tone = side === "UP" ? "bg-up" : "bg-down";
  const text = side === "UP" ? "text-up" : "text-down";

  return (
    <div className="rounded-lg border hairline bg-black/20 p-3.5">
      <div className="flex items-center justify-between">
        <SideBadge side={side} />
        <span className={cx("mono text-[20px] font-semibold leading-none", text)}>{fmtCents(ask)}</span>
      </div>
      <div className="relative mt-3 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
        {pct !== null ? <div className={cx("h-full rounded-full", tone)} style={{ width: `${pct}%`, opacity: armed ? 1 : 0.6 }} /> : null}
        {threshold !== null ? <div className="absolute inset-y-0 w-px bg-white/60" style={{ left: `${threshold * 100}%` }} /> : null}
      </div>
      <div className="mt-2 flex items-center justify-between text-[10px]">
        <span className="mono text-slate-600">trigger {fmtCents(threshold)}</span>
        {armed ? (
          <span className={cx("chip", side === "UP" ? "border-up/30 bg-up/10 text-up" : "border-down/30 bg-down/10 text-down")}>
            <IconZap size={9} />
            armed
          </span>
        ) : (
          <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
            {ask != null && threshold !== null && ask < threshold ? `${fmtCents(threshold - ask)} away` : "idle"}
          </span>
        )}
      </div>
    </div>
  );
}

function LiveEvaluation() {
  const { snapshot } = useBot();
  const strategy = snapshot?.strategy;
  const threshold = strategy?.trigger_threshold ?? null;
  const config = snapshot?.config ?? {};
  const armedSide = strategy?.up_armed ? "UP" : strategy?.down_armed ? "DOWN" : null;

  return (
    <section className="panel p-5 xl:col-span-5">
      <div className="flex items-start justify-between">
        <div>
          <div className="label">Live evaluation</div>
          <div className="mt-1 text-[15px] font-semibold tracking-tight text-slate-100">Current asks vs trigger</div>
        </div>
        <span className={cx("chip", strategy?.in_window ? "border-amber/30 bg-amber/10 text-amber" : "border-white/8 bg-white/[0.03] text-slate-500")}>
          <IconActivity size={10} />
          {strategy?.in_window ? "window" : "standby"}
        </span>
      </div>

      <div className="mt-4 space-y-3">
        <ArmMeter side="UP" ask={strategy?.up_ask} threshold={threshold} armed={strategy?.up_armed} />
        <ArmMeter side="DOWN" ask={strategy?.down_ask} threshold={threshold} armed={strategy?.down_armed} />
      </div>

      <div className="mt-4 rounded-lg border hairline bg-black/20 px-3 py-2.5">
        <div className="label">If triggered now</div>
        <div className="mt-1.5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {armedSide ? <SideBadge side={armedSide} size="sm" /> : <span className="chip border-white/8 bg-white/[0.03] text-slate-500">no signal</span>}
            <span className="text-[11.5px] text-slate-400">
              {armedSide ? "marketable BUY at current ask" : "engine holds — threshold not met"}
            </span>
          </div>
          <span className="mono text-[12px] text-slate-300">{fmtUsd(typeof config.trade_amount_usdc === "number" ? config.trade_amount_usdc : null)}</span>
        </div>
        <div className="mono mt-1.5 text-[10px] text-slate-600">
          max execution {fmtCents(typeof config.max_execution_price === "number" ? config.max_execution_price : null)}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between text-[11px]">
        <span className="text-slate-500">{strategy?.reason.replace(/_/g, " ") ?? "waiting"}</span>
        <span className="mono text-slate-400">
          {strategy?.remaining_seconds != null ? `T-${Math.max(0, Math.ceil(strategy.remaining_seconds))}s` : "—"}
        </span>
      </div>
    </section>
  );
}

function TriggerHistory() {
  const { dataVersion } = useBot();
  const triggers = useLiveRefresh(() => api.triggers(100), [], dataVersion);
  const rows = triggers.data ?? [];

  return (
    <section className="panel overflow-hidden xl:col-span-8">
      <div className="flex items-center justify-between border-b hairline px-5 py-4">
        <div>
          <div className="label">Trigger audit</div>
          <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Threshold events</div>
        </div>
        <div className="flex items-center gap-2">
          {triggers.error ? <span className="chip border-down/25 bg-down/10 text-down">api error</span> : null}
          <span className="chip border-white/8 bg-white/[0.03] text-slate-500">{rows.length} events</span>
        </div>
      </div>
      {rows.length ? (
        <div className="max-h-[420px] overflow-auto scrollbar">
          <table className="data-table">
            <thead className="sticky top-0 bg-ink-900/95 backdrop-blur">
              <tr>
                <th>Time</th>
                <th>Market</th>
                <th>Side</th>
                <th className="text-right">Ask</th>
                <th className="text-right">Ask size</th>
                <th className="text-right">Remaining</th>
                <th>Decision</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((trigger) => (
                <tr key={trigger.id}>
                  <td className="mono text-slate-500">{fmtTime(trigger.created_at)}</td>
                  <td className="mono text-slate-500" title={trigger.market_id}>
                    {shortId(trigger.market_id, 12, 4)}
                  </td>
                  <td>
                    <SideBadge side={trigger.side} size="sm" />
                  </td>
                  <td className="mono text-right text-slate-200">{fmtCents(trigger.best_ask)}</td>
                  <td className="mono text-right text-slate-500">
                    {trigger.ask_size != null ? fmtNum(trigger.ask_size, 0) : "—"}
                  </td>
                  <td className="mono text-right text-slate-400">{fmtNum(trigger.remaining_seconds, 1)}s</td>
                  <td>
                    <StatusChip status={trigger.decision} />
                  </td>
                  <td className="max-w-[260px] truncate text-slate-500" title={trigger.reason}>
                    {trigger.reason || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          icon={<IconTarget size={26} />}
          title="No trigger events recorded"
          hint="A row is written when an ask reaches the trigger, including blocked and skipped decisions."
        />
      )}
    </section>
  );
}

function PolicyPanel() {
  const { snapshot } = useBot();
  const strategy = snapshot?.strategy;
  const config = snapshot?.config ?? {};
  const rows: Array<[string, string]> = [
    ["Trigger threshold", fmtCents(strategy?.trigger_threshold ?? null)],
    ["Final window", strategy ? `${strategy.final_window_seconds}s` : "—"],
    [
      "Stage 0 (early buy + sell)",
      strategy?.stage0_enabled
        ? `≤${strategy.stage0_start_seconds}s · ask ${fmtCents(strategy.stage0_min_ask)}–${fmtCents(strategy.stage0_max_ask)} · gap ≥ $${strategy.stage0_min_price_gap_usd} · sell ${fmtCents(strategy.stage0_sell_price_low)} / ${fmtCents(strategy.stage0_sell_price_mid)} / ${fmtCents(strategy.stage0_sell_price)} (buy <${fmtCents(strategy.stage0_mid_from_ask)} / <${fmtCents(strategy.stage0_high_from_ask)} / above)`
        : "off",
    ],
    [
      "Stage 1 take-profit",
      strategy?.stage1_sell_enabled
        ? `bought ≤${fmtCents(strategy.stage1_sell_max_buy)} → sell ${fmtCents(strategy.stage1_sell_price)} · above → sell ${fmtCents(strategy.stage1_sell_price_high)}`
        : "off",
    ],
    [
      "Stage 2 take-profit",
      strategy?.stage2_sell_enabled ? `sell at ${fmtCents(strategy.stage2_sell_price)}` : "off",
    ],
    [
      "Stop-loss (stage 0 / 1 / 2 / 3)",
      strategy
        ? [
            strategy.stage0_stop_loss_pct,
            strategy.stage1_stop_loss_pct,
            strategy.stage2_stop_loss_pct,
            strategy.stage3_stop_loss_pct,
          ]
            .map((pct) => (pct > 0 ? `${pct}%` : "off"))
            .join(" / ")
        : "—",
    ],
    [
      "Stage 2 (after stage 1)",
      strategy ? `≤${strategy.stage2_seconds}s · ask ≥ ${fmtCents(strategy.stage2_threshold)}` : "—",
    ],
    [
      "Stage 3 (independent)",
      strategy ? `≤${strategy.stage3_seconds}s · ask ≥ ${fmtCents(strategy.stage3_threshold)}` : "—",
    ],
    ["Max execution price", fmtCents(typeof config.max_execution_price === "number" ? config.max_execution_price : null)],
    ["Trade size", fmtUsd(typeof config.trade_amount_usdc === "number" ? config.trade_amount_usdc : null)],
    ["Duplicate policy", String(strategy?.duplicate_policy ?? "—").replace(/_/g, " ")],
    ["Conflict policy", String(strategy?.conflict_policy ?? "—").replace(/_/g, " ")],
    ["Max trades / day", String(config.max_trades_per_day ?? "—")],
    ["Max open positions", String(config.max_open_positions ?? "—")],
    [
      "Min price gap (stage 1 / 2 / 3)",
      [config.min_price_gap_usd, config.stage2_min_price_gap_usd, config.stage3_min_price_gap_usd]
        .map((gap) => (typeof gap === "number" && gap > 0 ? `$${gap}` : "off"))
        .join(" / "),
    ],
    ["Daily loss stop", fmtUsd(typeof config.max_daily_loss === "number" ? config.max_daily_loss : null)],
    ["Daily profit target", fmtUsd(typeof config.daily_profit_target === "number" ? config.daily_profit_target : null)],
  ];

  return (
    <section className="panel p-5 xl:col-span-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="label">Guardrails</div>
          <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Policies & limits</div>
        </div>
        <IconShield size={18} className="text-slate-600" />
      </div>
      <div className="mt-4 divide-y divide-white/[0.04]">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between py-2 text-[11.5px]">
            <span className="text-slate-500">{label}</span>
            <span className="mono capitalize text-slate-300">{value}</span>
          </div>
        ))}
      </div>
      <div className="mt-4 rounded-lg border hairline bg-black/20 px-3 py-2.5 text-[10.5px] leading-relaxed text-slate-500">
        Values update live from the backend runtime settings. Secrets and the live confirmation phrase are never
        exposed through the dashboard API.
      </div>
    </section>
  );
}

export function Strategy() {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <PhaseTimeline />
      <LiveEvaluation />
      <TriggerHistory />
      <PolicyPanel />
    </div>
  );
}
