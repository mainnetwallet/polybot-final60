import type { Page } from "../lib/navigation";
import { NAV, PAGE_META } from "../lib/navigation";
import { useBot } from "../hooks/BotContext";
import { cx } from "../lib/format";
import { IconAlert, IconPause, IconPlay, IconShield, IconWifi, IconWifiOff, IconZap } from "./Icons";
import { StatusPill } from "./Badges";

interface TopbarProps {
  page: Page;
  onNavigate: (page: Page) => void;
  onOpenMode: () => void;
  onStartLive: () => void;
  onOpenKill: () => void;
}

export function Topbar({ page, onNavigate, onOpenMode, onStartLive, onOpenKill }: TopbarProps) {
  const { snapshot, feedState, start, stop, killSwitch: setKillSwitch } = useBot();
  const meta = PAGE_META[page];
  const bot = snapshot?.bot;
  const running = bot?.status === "RUNNING";
  const killActive = bot?.kill_switch ?? false;
  const mode = bot?.mode ?? "paper";

  const handleStart = () => {
    if (!snapshot) return;
    if (mode === "live") {
      onStartLive();
      return;
    }
    void start();
  };

  return (
    <header className="sticky top-0 z-30 border-b hairline bg-ink-950/80 backdrop-blur-xl">
      <div className="flex h-[68px] items-center gap-3 px-4 sm:px-6">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[9.5px] font-semibold uppercase tracking-[0.18em] text-slate-600">
            <span>Polybot</span>
            <span className="h-px w-4 bg-white/10" />
            <span className="text-slate-500">Final 60</span>
          </div>
          <div className="mt-0.5 flex items-center gap-2.5">
            <h1 className="truncate text-[16px] font-semibold tracking-tight text-slate-100">{meta.title}</h1>
            <span className="hidden truncate text-[11.5px] text-slate-600 xl:inline">{meta.subtitle}</span>
          </div>
        </div>

        <div className="hidden items-center gap-2 md:flex">
          <span
            className={cx(
              "chip border-white/8 bg-white/[0.03]",
              feedState === "open" ? "text-up" : feedState === "connecting" ? "text-amber" : "text-down",
            )}
            title="Backend state feed"
          >
            {feedState === "open" ? <IconWifi size={11} /> : <IconWifiOff size={11} />}
            {feedState === "open" ? "Live feed" : feedState === "connecting" ? "Connecting" : "Offline"}
          </span>
          <StatusPill running={running} killSwitch={killActive} />
          {bot?.risk_halt ? (
            <span className="chip border-amber/30 bg-amber/10 text-amber" title="Risk engine halt reason">
              <IconAlert size={10} />
              {bot.risk_halt.replace(/_/g, " ")}
            </span>
          ) : null}
        </div>

        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-white/8 bg-black/30 p-0.5" title="Execution mode">
            <button
              type="button"
              onClick={() => {
                if (mode !== "paper") onOpenMode();
              }}
              className={cx(
                "rounded-md px-2.5 py-1.5 text-[10.5px] font-bold uppercase tracking-[0.12em]",
                mode === "paper" ? "bg-sky/15 text-sky" : "text-slate-600 hover:text-slate-400",
              )}
            >
              Paper
            </button>
            <button
              type="button"
              onClick={() => {
                if (mode !== "live") onOpenMode();
              }}
              className={cx(
                "flex items-center gap-1 rounded-md px-2.5 py-1.5 text-[10.5px] font-bold uppercase tracking-[0.12em]",
                mode === "live" ? "bg-down/15 text-down" : "text-slate-600 hover:text-slate-400",
              )}
            >
              <IconZap size={9} />
              Live
            </button>
          </div>

          {running ? (
            <button type="button" onClick={() => void stop()} className="btn btn-danger h-9">
              <IconPause size={13} />
              <span className="hidden sm:inline">Stop bot</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={handleStart}
              disabled={!snapshot || killActive}
              title={
                killActive
                  ? "Clear the kill switch first"
                  : mode === "live"
                    ? "Start live engine (confirmation required)"
                    : "Start paper engine"
              }
              className="btn btn-primary h-9"
            >
              <IconPlay size={13} />
              <span className="hidden sm:inline">Start bot</span>
            </button>
          )}

          <button
            type="button"
            onClick={() => (killActive ? void setKillSwitch(false) : onOpenKill())}
            className={cx("btn h-9", killActive ? "border-amber/35 bg-amber/10 text-amber" : "btn-danger")}
            title={killActive ? "Clear the emergency kill switch" : "Emergency kill switch"}
          >
            <IconShield size={14} />
            <span className="hidden xl:inline">{killActive ? "Clear kill" : "Kill switch"}</span>
          </button>
        </div>
      </div>

      <div className="flex gap-1 overflow-x-auto px-3 pb-2 lg:hidden">
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onNavigate(item.id)}
              className={cx(
                "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] font-medium",
                item.id === page ? "bg-white/8 text-slate-100" : "text-slate-500",
              )}
            >
              <Icon size={12} />
              {item.label}
            </button>
          );
        })}
      </div>
    </header>
  );
}
