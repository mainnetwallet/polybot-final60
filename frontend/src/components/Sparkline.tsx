import { useId } from "react";
import { cx } from "../lib/format";

type Tone = "up" | "down" | "sky" | "amber" | "muted";

const TONE_TEXT: Record<Tone, string> = {
  up: "text-up",
  down: "text-down",
  sky: "text-sky",
  amber: "text-amber",
  muted: "text-slate-500",
};

interface SparklineProps {
  data: number[];
  tone?: Tone;
  height?: number;
  width?: number;
  className?: string;
  fill?: boolean;
}

export function Sparkline({
  data,
  tone = "sky",
  height = 42,
  width = 160,
  className,
  fill = true,
}: SparklineProps) {
  const rawId = useId();
  const gradientId = `spark${rawId.replace(/[^a-zA-Z0-9]/g, "")}`;

  const values = data.length ? data : [0.5];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 0.01;
  const pad = 3;

  const points = values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - pad - ((value - min) / span) * (height - pad * 2);
    return [x, y] as const;
  });

  const line = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `M 0,${height} L ${points
    .map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`)
    .join(" L ")} L ${width},${height} Z`;
  const last = points[points.length - 1];

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cx("block w-full", TONE_TEXT[tone], className)}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.24" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      {fill ? <path d={area} fill={`url(#${gradientId})`} /> : null}
      <polyline
        points={line}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={last[0]} cy={last[1]} r={2.4} fill="currentColor" />
    </svg>
  );
}
