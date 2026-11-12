import type { ReactNode } from "react";
import type { Mode, Side } from "../api/types";
import { cx } from "../lib/format";
import { IconArrowDown, IconArrowUp, IconLock, IconZap } from "./Icons";

export function StatusPill({ running, killSwitch }: { running: boolean; killSwitch: boolean }) {
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
      return "border-amber/30 bg-amber/10 text-amber";
    case "SUBMITTED":
    case "submitted_pending":
    case "armed":
      return "border-sky/30 bg-sky/10 text-sky";
    case "REJECTED":
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

export function StatusChip({ status }: { status: string }) {
  const label = status.replace(/_/g, " ");
  return <span className={cx("chip", statusTone(status))}>{label}</span>;
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

export function KeyValue({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={cx("mt-1 text-[13px] text-slate-200", mono && "mono text-[12px]")}>{value}</div>
    </div>
  );
}
