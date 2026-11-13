export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function isNum(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function fmtUsd(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return "—";
  return `${value < 0 ? "-" : ""}$${Math.abs(value).toFixed(digits)}`;
}

export function fmtUsdGrouped(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return "—";
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function fmtSignedUsd(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toFixed(digits)}`;
}

export function fmtCents(value: number | null | undefined, digits = 1): string {
  if (!isNum(value)) return "—";
  return `${(value * 100).toFixed(digits)}¢`;
}

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return "—";
  return value.toFixed(digits);
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function ago(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const ms = typeof value === "number" ? value : Date.parse(value);
  if (Number.isNaN(ms)) return "—";
  const delta = Math.max(0, Date.now() - ms);
  if (delta < 1000) return "now";
  if (delta < 60_000) return `${Math.floor(delta / 1000)}s ago`;
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  return `${Math.floor(delta / 3_600_000)}h ago`;
}

export function shortId(value: string | null | undefined, head = 8, tail = 4): string {
  if (!value) return "—";
  if (value.length <= head + tail + 1) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function pnlClass(value: number | null | undefined): string {
  if (!isNum(value) || value === 0) return "text-slate-300";
  return value > 0 ? "text-up" : "text-down";
}
