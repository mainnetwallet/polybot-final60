import type { ComponentType } from "react";
import {
  IconAnalytics,
  IconLogs,
  IconOverview,
  IconSettings,
  IconStrategy,
  IconTrading,
} from "../components/Icons";

export type Page = "overview" | "strategy" | "trading" | "analytics" | "settings" | "logs";

export interface NavItem {
  id: Page;
  label: string;
  icon: ComponentType<{ size?: number; className?: string }>;
}

export const NAV: NavItem[] = [
  { id: "overview", label: "Overview", icon: IconOverview },
  { id: "strategy", label: "Strategy monitor", icon: IconStrategy },
  { id: "trading", label: "Trading", icon: IconTrading },
  { id: "analytics", label: "Analytics", icon: IconAnalytics },
  { id: "settings", label: "Settings", icon: IconSettings },
  { id: "logs", label: "Logs", icon: IconLogs },
];

export const PAGE_META: Record<Page, { title: string; subtitle: string }> = {
  overview: {
    title: "Command center",
    subtitle: "BTC 5-minute UP/DOWN \u00b7 final 60 seconds \u00b7 marketable buys",
  },
  strategy: {
    title: "Strategy monitor",
    subtitle: "FINAL_60_SECONDS_MARKET_BUY \u00b7 observation, window, trigger",
  },
  trading: {
    title: "Trading desk",
    subtitle: "Orders, fills, positions and execution quality",
  },
  analytics: {
    title: "Performance analytics",
    subtitle: "Mode-separated PnL, win rate and volume",
  },
  settings: {
    title: "Configuration",
    subtitle: "Runtime strategy, risk and simulation parameters",
  },
  logs: {
    title: "System logs",
    subtitle: "Live event stream persisted to the local database",
  },
};

export function pageFromHash(): Page {
  const hash = window.location.hash.replace("#", "").trim();
  return (NAV.find((item) => item.id === hash)?.id ?? "overview") as Page;
}
