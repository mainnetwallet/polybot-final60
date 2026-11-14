import type { Mode, Side } from "../api/types";
import { cx } from "../lib/format";
import { IconArrowDown, IconArrowUp, IconLock, IconZap } from "./Icons";

export function StatusPill({ running, killSwitch }: { running: boolean | null; killSwitch: boolean | null }) {
  if (running === null || killSwitch === null) {
    return <span className="chip border-white/10 bg-white/5 text-muted">Loading state</span>;
  }
  if (killSwitch) {
    return (
      <span className="chip border-down/35 bg-down/10 text-down">
        <span className="h-1.5 w-1.5 rounded-full bg-down" />
        Kill switch
      </span>
    );
  }
  if (running) {
    return (
      <span className="chip border-up/30 bg-up/10 text-up">
        <span className="live-dot h-1.5 w-1.5 rounded-full bg-up" />
        Running
      </span>
    );
  }
  return (
    <span className="chip border-white/10 bg-white/5 text-muted">
      <span className="h-1.5 w-1.5 rounded-full bg-slate-500" />
      Stopped
    </span>
  );
}

export function ModeBadge({ mode, size = "md" }: { mode: Mode; size?: "sm" | "md" }) {
  const live = mode === "live";
  return (
    <span
      className={cx(
        "chip",
        size === "sm" ? "px-2 py-0.5 text-[9px]" : "",
        live ? "border-down/35 bg-down/10 text-down" : "border-sky/25 bg-sky/10 text-sky",
      )}
      title={live ? "Live execution with real funds" : "Paper execution on live market data"}
    >
      {live ? <IconZap size={10} /> : <IconLock size={10} />}
      {live ? "Live" : "Paper"}
    </span>
  );
}

export function SideBadge({ side, size = "md" }: { side: Side; size?: "sm" | "md" }) {
  const up = side === "UP";
  return (
    <span
      className={cx(
        "chip",
        size === "sm" ? "px-2 py-0.5 text-[9px]" : "",
        up ? "border-up/30 bg-up/10 text-up" : "border-down/30 bg-down/10 text-down",
      )}
    >
      {up ? <IconArrowUp size={10} /> : <IconArrowDown size={10} />}
      {side}
    </span>
  );
}

function statusTone(status: string): string {
  switch (status) {
    case "FILLED":
    case "filled":
    case "submitted":
      return "border-up/30 bg-up/10 text-up";
    case "PARTIALLY_FILLED":
    case "partially_filled":
    case "PENDING":
    case "pending":
    case "settling":
      return "border-amber/30 bg-amber/10 text-amber";
    case "SUBMITTED":
    case "submitted_pending":
    case "armed":
      return "border-sky/30 bg-sky/10 text-sky";
    case "REJECTED":
    case "CANCELED":
    case "CANCELLED":
    case "FAILED":
    case "EXPIRED":
    case "rejected":
    case "blocked":
    case "expired":
      return "border-down/30 bg-down/10 text-down";
    default:
      if (status.startsWith("blocked") || status.startsWith("failed")) {
        return "border-down/30 bg-down/10 text-down";
      }
      if (status.startsWith("skipped")) {
        return "border-amber/30 bg-amber/10 text-amber";
      }
      if (status.startsWith("filled")) {
        return "border-up/30 bg-up/10 text-up";
      }
      return "border-white/10 bg-white/5 text-slate-400";
  }
}

export function StatusChip({
  status,
  action,
  reason,
}: {
  status: string;
  action?: string | null;
  reason?: string | null;
}) {
  const label = status.replace(/_/g, " ");
  // Orders: tag the status with what kind of order it is (BUY / STOP LOSS / TAKE PROFIT).
  let kind: string | null = null;
  let exitTone: string | null = null;
  if (action === "BUY") {
    kind = "BUY";
  } else if (action === "SELL") {
    if (reason?.endsWith("_stop_loss")) {
      kind = "STOP LOSS";
      exitTone = "border-rose-400/40 bg-rose-400/15 text-rose-300"; // red
    } else if (reason?.endsWith("_manual")) {
      kind = "MANUAL SELL";
      exitTone = "border-amber/40 bg-amber/15 text-amber"; // amber
    } else if (reason?.endsWith("_take_profit")) {
      kind = "TAKE PROFIT";
      exitTone = "border-cyan-400/45 bg-cyan-400/15 text-cyan-300"; // cyan
    }
  }
  // A filled exit gets its own color per kind; every other status keeps the normal status color.
  const tone = status === "FILLED" && exitTone ? exitTone : statusTone(status);
  return (
    <span className={cx("chip", tone)}>
      {kind ? `${kind} ${label}` : label}
    </span>
  );
}

export function StageChip({ stage, action }: { stage?: number | null; action?: string | null }) {
  if (stage == null) return <span className="text-slate-600">—</span>;
  const tone =
    stage === 0
      ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
      : stage === 1
      ? "border-sky-400/30 bg-sky-400/10 text-sky-300"
      : stage === 2
        ? "border-amber/30 bg-amber/10 text-amber"
        : "border-violet-400/30 bg-violet-400/10 text-violet-300";
  return (
    <span
      className={cx("chip", tone)}
      title={action === "SELL" ? `Stage ${stage} exit sale (take-profit or stop-loss)` : `Entry stage ${stage}`}
    >
      S{stage}
    </span>
  );
}

export function ResultChip({ result, pnl }: { result?: string | null; pnl?: number | null }) {
  if (!result) return <span className="text-slate-600">—</span>;
  const tone =
    result === "WIN"
      ? "border-up/30 bg-up/10 text-up"
      : result === "LOSS"
        ? "border-down/30 bg-down/10 text-down"
        : result === "BUY"
          ? "border-sky/30 bg-sky/10 text-sky"
          : "border-amber/30 bg-amber/10 text-amber";
  const label = result === "OPEN" ? "PENDING" : result;
  const sign = pnl != null && pnl > 0 ? "+" : "";
  const title =
    result === "BUY"
      ? "Entry filled. The WIN/LOSS result is shown on the SELL row."
      : pnl != null
        ? `Realized P&L ${sign}$${pnl.toFixed(2)}`
        : "Waiting for market settlement";
  return (
    <span className={cx("chip", tone)} title={title}>
      {label}
      {pnl != null ? ` ${sign}$${pnl.toFixed(2)}` : ""}
    </span>
  );
}

export function LevelChip({ level }: { level: string }) {
  const tone =
    level === "ERROR" || level === "CRITICAL"
      ? "border-down/30 bg-down/10 text-down"
      : level === "WARNING"
        ? "border-amber/30 bg-amber/10 text-amber"
        : "border-sky/25 bg-sky/10 text-sky";
  return <span className={cx("chip font-mono", tone)}>{level}</span>;
}
