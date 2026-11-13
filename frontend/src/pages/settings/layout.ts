import type { FieldSpec } from "./fields";
import { FIELDS } from "./fields";

export type TabId = "entry" | "stage0" | "exits" | "risk" | "execution" | "maintenance";

export interface FieldGroup {
  title: string;
  description?: string;
  keys: string[];
}

export interface TabSpec {
  id: TabId;
  label: string;
  title: string;
  description: string;
  groups: FieldGroup[];
}

// Every field is placed by key, so adding a field to FIELDS never lands in the wrong card.
export const TABS: TabSpec[] = [
  {
    id: "entry",
    label: "Entry",
    title: "Entry logic",
    description: "When the final-window stages fire a marketable BUY.",
    groups: [
      {
        title: "Timing",
        description: "How many seconds before expiry each stage becomes active.",
        keys: ["final_window_seconds", "stage2_seconds", "stage3_seconds"],
      },
      {
        title: "Ask triggers",
        description: "Best ask at or above this decimal activates the stage.",
        keys: ["trigger_threshold", "stage2_threshold", "stage3_threshold"],
      },
      {
        title: "BTC price-gap filters",
        description: "Require BTC to be this far beyond the price to beat. 0 turns the filter off.",
        keys: ["min_price_gap_usd", "stage2_min_price_gap_usd", "stage3_min_price_gap_usd"],
      },
      {
        title: "Order handling",
        keys: ["duplicate_policy", "conflict_policy"],
      },
    ],
  },
  {
    id: "stage0",
    label: "Stage 0",
    title: "Early buy + take-profit",
    description: "Buys before the final window and sells on a tiered take-profit.",
    groups: [
      { title: "Activation", keys: ["stage0_enabled", "stage0_start_seconds"] },
      {
        title: "Entry band",
        description: "Ask range and BTC gap in which stage 0 may buy.",
        keys: ["stage0_min_ask", "stage0_max_ask", "stage0_min_price_gap_usd"],
      },
      {
        title: "Take-profit tiers",
        description: "Buy price decides the tier. 0 % means hold to settlement.",
        keys: [
          "stage0_mid_from_ask",
          "stage0_high_from_ask",
          "stage0_take_profit_pct_low",
          "stage0_take_profit_pct_mid",
          "stage0_take_profit_pct_high",
        ],
      },
    ],
  },
  {
    id: "exits",
    label: "Exits",
    title: "Take-profit & stop-loss",
    description: "How open shares are closed before settlement.",
    groups: [
      {
        title: "Stage 1 take-profit",
        keys: ["stage1_sell_enabled", "stage1_sell_max_buy", "stage1_sell_price", "stage1_sell_price_high"],
      },
      { title: "Stage 2 take-profit", keys: ["stage2_sell_enabled", "stage2_sell_price"] },
      {
        title: "Stop-loss",
        description: "Market sell once the best bid is this many percent below the average buy. 0 = off.",
        keys: ["stage0_stop_loss_pct", "stage1_stop_loss_pct", "stage2_stop_loss_pct", "stage3_stop_loss_pct"],
      },
    ],
  },
  {
    id: "risk",
    label: "Risk",
    title: "Risk & guardrails",
    description: "Position sizing, daily limits and data-quality protection.",
    groups: [
      { title: "Sizing", keys: ["trade_amount_usdc", "max_execution_price"] },
      {
        title: "Daily limits",
        keys: ["max_daily_loss", "daily_profit_target", "max_trades_per_day", "max_open_positions"],
      },
      { title: "Data quality", keys: ["stale_data_max_age_ms"] },
    ],
  },
  {
    id: "execution",
    label: "Execution",
    title: "Execution & live safety",
    description: "Paper fill model, live readiness gates and connection check.",
    groups: [{ title: "Paper fill model", keys: ["paper_slippage_bps", "paper_fee_rate"] }],
  },
  {
    id: "maintenance",
    label: "Maintenance",
    title: "Maintenance & resets",
    description: "Reset counters, the paper ledger or the whole database.",
    groups: [],
  },
];

const BY_KEY = new Map<string, FieldSpec>(FIELDS.map((field) => [field.key, field]));

export function fieldsOf(keys: string[]): FieldSpec[] {
  return keys.map((key) => BY_KEY.get(key)).filter((field): field is FieldSpec => !!field);
}

export function tabOfField(key: string): TabId | null {
  for (const tab of TABS) {
    if (tab.groups.some((group) => group.keys.includes(key))) return tab.id;
  }
  return null;
}
