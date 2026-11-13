import { clamp, cx } from "../lib/format";

interface CountdownRingProps {
  remaining: number | null;
  inWindow: boolean;
  finalWindow: number;
  duration: number | null;
  armed: boolean;
  stage0Enabled?: boolean;
  stage0Start?: number;
  stage2Seconds?: number;
  stage3Seconds?: number;
  size?: number;
}

/** Which entry stage is live right now, from the seconds left in the market. */
export function activeStageLabel(
  remaining: number,
  finalWindow: number,
  stage0Enabled: boolean,
  stage0Start: number,
  stage2Seconds: number,
  stage3Seconds: number,
): string {
  if (remaining <= 0) return "EXPIRED";
  if (remaining <= stage3Seconds) return "STAGE 3";
  if (remaining <= stage2Seconds) return "STAGE 2";
  if (remaining <= finalWindow) return "STAGE 1";
  if (stage0Enabled && remaining <= stage0Start) return "STAGE 0";
  return "WAITING";
}

export function CountdownRing({
  remaining,
  inWindow,
  finalWindow,
  duration,
  armed,
  stage0Enabled = false,
  stage0Start = 0,
  stage2Seconds = 50,
  stage3Seconds = 30,
  size = 176,
}: CountdownRingProps) {
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  // The ring counts the whole market down (e.g. 300 -> 0); the label says which stage is running.
  const shown = remaining === null ? null : Math.max(0, remaining);

  let progress = 0;
  if (remaining !== null && duration && duration > 0) {
    progress = clamp(remaining / duration, 0, 1);
  }

  const tone = inWindow
    ? armed
      ? "text-up"
      : "text-amber"
    : remaining === null
      ? "text-slate-600"
      : "text-sky";

  const label =
    remaining === null
      ? "NO ACTIVE MARKET"
      : activeStageLabel(remaining, finalWindow, stage0Enabled, stage0Start, stage2Seconds, stage3Seconds);

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
          seconds left
        </div>
      </div>
    </div>
  );
}
