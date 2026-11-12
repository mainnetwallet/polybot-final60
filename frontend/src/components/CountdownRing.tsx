import { clamp, cx } from "../lib/format";

interface CountdownRingProps {
  remaining: number | null;
  inWindow: boolean;
  finalWindow: number;
  duration: number | null;
  armed: boolean;
  size?: number;
}

export function CountdownRing({
  remaining,
  inWindow,
  finalWindow,
  duration,
  armed,
  size = 176,
}: CountdownRingProps) {
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  const shown = inWindow
    ? Math.max(0, remaining ?? 0)
    : remaining === null
      ? null
      : Math.max(0, remaining - finalWindow);

  let progress = 0;
  if (remaining !== null) {
    if (inWindow) {
      progress = clamp(remaining / Math.max(1, finalWindow), 0, 1);
    } else if (duration && duration > finalWindow) {
      progress = clamp((duration - remaining) / (duration - finalWindow), 0, 1);
    }
  }

  const tone = inWindow
    ? armed
      ? "text-up"
      : "text-amber"
    : remaining === null
      ? "text-slate-600"
      : "text-sky";

  const label = remaining === null ? "NO ACTIVE MARKET" : inWindow ? "FINAL WINDOW" : "TO FINAL WINDOW";

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox="0 0 128 128" className={cx("h-full w-full -rotate-90", tone)} aria-hidden="true">
        <circle cx="64" cy="64" r={radius} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="7" />
        <circle
          cx="64"
          cy="64"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - progress)}
          style={{
            filter: inWindow ? "drop-shadow(0 0 7px currentColor)" : undefined,
          }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className="label !text-[8.5px] text-center px-2">{label}</div>
        <div className={cx("mono mt-1 text-[38px] font-semibold leading-none tracking-tight", tone)}>
          {shown === null ? "—" : Math.ceil(shown)}
        </div>
        <div className="mt-1 text-[9px] font-semibold uppercase tracking-[0.2em] text-slate-600">
          {inWindow ? "seconds left" : "seconds"}
        </div>
      </div>
    </div>
  );
}
