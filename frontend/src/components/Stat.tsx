import type { ReactNode } from "react";
import { cx } from "../lib/format";

type Tone = "default" | "up" | "down" | "sky" | "amber";

const TONE_TEXT: Record<Tone, string> = {
  default: "text-slate-100",
  up: "text-up",
  down: "text-down",
  sky: "text-sky",
  amber: "text-amber",
};

interface StatProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
  icon?: ReactNode;
  className?: string;
}

export function Stat({ label, value, hint, tone = "default", icon, className }: StatProps) {
  return (
    <div className={cx("min-w-0", className)}>
      <div className="flex items-center gap-1.5">
        <span className="label">{label}</span>
        {icon ? <span className="text-slate-600">{icon}</span> : null}
      </div>
      <div className={cx("mt-1.5 truncate font-mono text-[22px] font-semibold leading-none tracking-tight", TONE_TEXT[tone])}>
        {value}
      </div>
      {hint ? <div className="mt-1.5 truncate text-[11px] text-muted">{hint}</div> : null}
    </div>
  );
}

export function StatGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-2">{children}</div>
  );
}
