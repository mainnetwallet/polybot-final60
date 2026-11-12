import { useBot } from "../hooks/BotContext";
import { useRemainingSeconds } from "../hooks/useCountdown";
import { cx, fmtUsdGrouped, isNum } from "../lib/format";

function TimeUnit({ value, label, urgent }: { value: string; label: string; urgent: boolean }) {
  return (
    <div className="flex flex-col items-center">
      <div
        className={cx(
          "mono text-[34px] font-semibold leading-none tracking-tight tabular-nums",
          urgent ? "text-down" : "text-slate-100",
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-[9px] font-semibold uppercase tracking-[0.18em] text-slate-500">{label}</div>
    </div>
  );
}

/** Price to beat · live BTC price (with gap) · MINS/SECS countdown. */
export function PriceToBeatBar({ className }: { className?: string }) {
  const { snapshot } = useBot();
  const remaining = useRemainingSeconds();
  const btc = snapshot?.btc;
  const finalWindow = snapshot?.strategy.final_window_seconds ?? 60;

  const beat = btc?.price_to_beat ?? null;
  const price = btc?.price ?? null;
  const diff = isNum(btc?.diff) ? btc.diff : null;
  const above = diff !== null && diff >= 0;

  const total = remaining === null ? null : Math.max(0, Math.ceil(remaining));
  const mins = total === null ? "--" : String(Math.floor(total / 60)).padStart(2, "0");
  const secs = total === null ? "--" : String(total % 60).padStart(2, "0");
  const urgent = remaining !== null && remaining <= finalWindow;

  return (
    <section className={cx("panel flex flex-wrap items-center justify-between gap-x-8 gap-y-4 px-6 py-4", className)}>
      <div className="flex items-center gap-6">
        <div>
          <div className="text-[12px] font-semibold text-slate-400">Price To Beat</div>
          <div className="mono mt-1 text-[26px] font-semibold leading-none tracking-tight text-slate-300">
            {fmtUsdGrouped(beat)}
          </div>
        </div>

        <div className="h-10 w-px bg-white/10" />

        <div>
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-semibold text-amber">Current Price</span>
            {diff !== null ? (
              <span className={cx("mono flex items-center gap-1 text-[11px] font-semibold", above ? "text-up" : "text-down")}>
                <span aria-hidden="true">{above ? "▲" : "▼"}</span>
                {`$${Math.round(Math.abs(diff)).toLocaleString("en-US")}`}
              </span>
            ) : null}
          </div>
          <div className={cx("mono mt-1 text-[26px] font-semibold leading-none tracking-tight", btc?.stale ? "text-slate-500" : "text-amber")}>
            {fmtUsdGrouped(price)}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-4">
        {btc && (!btc.connected || btc.stale) && btc.enabled ? (
          <span className="chip border-amber/30 bg-amber/10 text-amber" title={btc.last_error ?? undefined}>
            {btc.connected ? "stale price" : "price feed offline"}
          </span>
        ) : null}
        <TimeUnit value={mins} label="Mins" urgent={urgent} />
        <TimeUnit value={secs} label="Secs" urgent={urgent} />
      </div>
    </section>
  );
}
