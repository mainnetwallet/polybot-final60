import { api } from "../api/client";
import { ModeBadge } from "../components/Badges";
import { IconActivity, IconAnalytics, IconTarget } from "../components/Icons";
import { Stat } from "../components/Stat";
import { useBot } from "../hooks/BotContext";
import { usePolled } from "../hooks/usePolled";
import { clamp, cx, fmtCents, fmtSignedUsd, fmtUsd, pnlClass } from "../lib/format";

export function Analytics() {
  const { snapshot, dataVersion } = useBot();
  const mode = snapshot?.bot.mode ?? "paper";
  const polled = usePolled(async () => {
    return { analytics: await api.analytics(mode) };
  }, 5000, [mode], dataVersion);

  const analytics = polled.data?.analytics;
  const closed = (analytics?.wins ?? 0) + (analytics?.losses ?? 0);
  const winPct = closed > 0 ? ((analytics?.wins ?? 0) / closed) * 100 : 0;

  const pnlRows = [
    { label: "Realized", value: analytics?.realized_pnl ?? 0 },
    { label: "Unrealized", value: analytics?.unrealized_pnl ?? 0 },
    { label: "Total", value: analytics?.total_pnl ?? 0 },
  ];
  const maxAbs = Math.max(0.01, ...pnlRows.map((row) => Math.abs(row.value)));

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <section className="panel flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between xl:col-span-12">
        <div className="flex items-center gap-3">
          {snapshot ? <ModeBadge mode={snapshot.bot.mode} /> : null}
          <div>
            <div className="text-[13px] font-semibold text-slate-100">Mode-separated performance</div>
            <div className="mt-0.5 text-[11px] text-slate-500">
              Paper and live balances, fills and statistics never mix. This view reads only the {mode} ledger.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-slate-500">
          {polled.error ? <span className="chip border-down/25 bg-down/10 text-down">api error</span> : null}
          <span className="chip border-white/8 bg-white/[0.03] text-slate-500">
            <IconActivity size={10} />
            {analytics?.total_trades ?? 0} trades
          </span>
        </div>
      </section>

      <section className="panel p-5 xl:col-span-12">
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="Realized PnL" value={fmtSignedUsd(analytics?.realized_pnl)} tone={(analytics?.realized_pnl ?? 0) >= 0 ? "up" : "down"} hint="settled markets" />
          <Stat label="Unrealized PnL" value={fmtSignedUsd(analytics?.unrealized_pnl)} tone={(analytics?.unrealized_pnl ?? 0) >= 0 ? "up" : "down"} hint="marked at best bid" />
          <Stat label="Fees paid" value={fmtUsd(analytics?.fees_paid ?? 0, 3)} hint="all fills" />
          <Stat label="Volume" value={fmtUsd(analytics?.volume_usdc ?? 0)} hint="price × size" />
          <Stat label="Avg entry" value={fmtCents(analytics?.avg_entry_price)} hint="size-weighted" />
        </div>
      </section>

      <section className="panel p-5 xl:col-span-4">
        <div className="flex items-start justify-between">
          <div>
            <div className="label">Outcomes</div>
            <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Win rate</div>
          </div>
          <IconTarget size={18} className="text-slate-600" />
        </div>
        <div className="mt-5 flex items-baseline gap-2">
          <span className={cx("mono text-[38px] font-semibold leading-none", winPct >= 50 ? "text-up" : "text-slate-200")}>
            {closed ? `${winPct.toFixed(1)}%` : "—"}
          </span>
          <span className="text-[11px] text-slate-500">{closed} settled</span>
        </div>
        <div className="mt-4 flex h-2.5 overflow-hidden rounded-full bg-white/[0.05]">
          {closed ? (
            <>
              <div className="bg-up/80" style={{ width: `${clamp(winPct, 0, 100)}%` }} />
              <div className="bg-down/70" style={{ width: `${clamp(100 - winPct, 0, 100)}%` }} />
            </>
          ) : (
            <div className="h-full w-full bg-white/[0.06]" />
          )}
        </div>
        <div className="mt-3 flex items-center justify-between text-[11px]">
          <span className="text-up">{analytics?.wins ?? 0} wins</span>
          <span className="text-down">{analytics?.losses ?? 0} losses</span>
        </div>
        <div className="mt-4 border-t hairline pt-3 text-[10.5px] leading-relaxed text-slate-500">
          A position wins when its realized payout exceeds cost basis and fees after resolution.
        </div>
      </section>

      <section className="panel p-5 xl:col-span-4">
        <div className="flex items-start justify-between">
          <div>
            <div className="label">PnL composition</div>
            <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Realized vs marked</div>
          </div>
          <IconAnalytics size={18} className="text-slate-600" />
        </div>
        <div className="mt-5 space-y-4">
          {pnlRows.map((row) => {
            const width = clamp((Math.abs(row.value) / maxAbs) * 100, 0, 100);
            return (
              <div key={row.label}>
                <div className="flex items-center justify-between text-[11px]">
                  <span className={row.label === "Total" ? "font-semibold text-slate-200" : "text-slate-500"}>
                    {row.label}
                  </span>
                  <span className={cx("mono", pnlClass(row.value))}>{fmtSignedUsd(row.value)}</span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                  <div
                    className={cx("h-full rounded-full", row.value >= 0 ? "bg-up" : "bg-down", row.label === "Total" ? "" : "opacity-70")}
                    style={{ width: `${width}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-4 border-t hairline pt-3 text-[10.5px] leading-relaxed text-slate-500">
          Unrealized PnL marks open positions at the current best bid; it is not withdrawable until resolution.
        </div>
      </section>

    </div>
  );
}
