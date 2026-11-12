import { useBot } from "../hooks/BotContext";
import { cx, fmtSignedUsd, fmtUsd, pnlClass } from "../lib/format";
import { NAV, type Page } from "../lib/navigation";
import { ModeBadge } from "./Badges";
import { IconLock, IconShield, IconTarget } from "./Icons";

interface SidebarProps {
  page: Page;
  onNavigate: (page: Page) => void;
}

export function Sidebar({ page, onNavigate }: SidebarProps) {
  const { snapshot, feedState } = useBot();
  const account = snapshot?.account;
  const bot = snapshot?.bot;

  return (
    <aside className="hidden w-[236px] shrink-0 flex-col border-r hairline bg-ink-950/55 px-3.5 pb-4 pt-5 lg:flex">
      <div className="flex items-center gap-2.5 px-2">
        <div className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-up to-emerald-600 text-[13px] font-black tracking-tight text-[#04140d] shadow-[0_0_24px_-6px_rgba(53,224,161,0.8)]">
          F60
        </div>
        <div>
          <div className="text-[13px] font-semibold tracking-tight text-slate-100">FINAL60</div>
          <div className="text-[9.5px] font-semibold uppercase tracking-[0.16em] text-slate-600">
            Polymarket BTC 5m
          </div>
        </div>
      </div>

      <nav className="mt-7 flex flex-col gap-1">
        <div className="label mb-2 px-2">Workspace</div>
        {NAV.map((item) => {
          const Icon = item.icon;
          const active = item.id === page;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onNavigate(item.id)}
              className={cx(
                "group flex items-center gap-3 rounded-lg px-2.5 py-2 text-left text-[12.5px] font-medium",
                active
                  ? "bg-white/[0.07] text-slate-100"
                  : "text-slate-400 hover:bg-white/[0.03] hover:text-slate-200",
              )}
            >
              <span
                className={cx(
                  "flex h-6 w-6 items-center justify-center rounded-md border",
                  active ? "border-up/30 bg-up/10 text-up" : "border-white/5 bg-white/[0.02] text-slate-500 group-hover:text-slate-300",
                )}
              >
                <Icon size={14} />
              </span>
              <span className="flex-1">{item.label}</span>
              {item.id === "strategy" ? (
                <span className="mono rounded border border-white/6 px-1 py-0.5 text-[8px] text-slate-600">60s</span>
              ) : null}
            </button>
          );
        })}
      </nav>

      <div className="mt-6 rounded-xl border hairline bg-white/[0.02] p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
            <IconTarget size={12} />
            Strategy
          </div>
          <IconLock size={11} className="text-slate-600" />
        </div>
        <div className="mt-2 text-[11.5px] font-semibold leading-snug text-slate-200">
          FINAL_60_SECONDS
          <br />
          MARKET_BUY
        </div>
        <div className="mt-2.5 space-y-1.5 text-[10.5px] text-slate-500">
          <div className="flex items-center justify-between">
            <span>First 4 min</span>
            <span className="text-slate-400">no trades</span>
          </div>
          <div className="flex items-center justify-between">
            <span>Final window</span>
            <span className="mono text-slate-400">{snapshot ? `${snapshot.strategy.final_window_seconds}s` : "—"}</span>
          </div>
          <div className="flex items-center justify-between">
            <span>Trigger ask</span>
            <span className="mono text-slate-400">
              {snapshot ? snapshot.strategy.trigger_threshold.toFixed(2) : "—"}
            </span>
          </div>
        </div>
      </div>

      <div className="mt-auto space-y-3 pt-5">
        <div className="rounded-xl border hairline bg-white/[0.02] p-3">
          <div className="flex items-center justify-between">
            <span className="label">Account</span>
            {bot ? <ModeBadge mode={bot.mode} size="sm" /> : null}
          </div>
          <div className="mono mt-2 text-[20px] font-semibold leading-none text-slate-100">
            {fmtUsd(account?.balance)}
          </div>
          <div className="mt-2 flex items-center justify-between text-[10.5px]">
            <span className="text-slate-600">Today</span>
            <span className={cx("mono font-semibold", pnlClass(account?.daily_pnl))}>
              {fmtSignedUsd(account?.daily_pnl)}
            </span>
          </div>
        </div>

        <div className="flex items-center justify-between px-1 text-[9.5px] text-slate-600">
          <span className="flex items-center gap-1.5">
            <span
              className={cx(
                "h-1.5 w-1.5 rounded-full",
                feedState === "open" ? "live-dot bg-up" : feedState === "connecting" ? "bg-amber" : "bg-down",
              )}
            />
            {feedState === "open" ? "Realtime feed" : feedState === "connecting" ? "Connecting" : "Feed offline"}
          </span>
          <span className="mono">v1.0.0</span>
        </div>
        <div className="flex items-center gap-1.5 px-1 text-[9px] uppercase tracking-[0.14em] text-slate-700">
          <IconShield size={10} />
          Local execution only
        </div>
      </div>
    </aside>
  );
}
