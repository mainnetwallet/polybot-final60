import type { SettingsState } from "../../api/types";
import { FIELDS, type FieldSpec } from "./fields";

export type Draft = Record<string, string>;

export function draftFrom(settings: SettingsState): Draft {
  const draft: Draft = {};
  for (const field of FIELDS) {
    draft[field.key] = String(settings[field.key as keyof SettingsState] ?? "");
  }
  return draft;
}

export function savedValue(settings: SettingsState, key: string): string {
  return String(settings[key as keyof SettingsState] ?? "");
}

export function isFieldDirty(field: FieldSpec, draft: Draft, settings: SettingsState): boolean {
  const raw = draft[field.key] ?? "";
  const saved = savedValue(settings, field.key);
  if (field.kind === "number") {
    if (raw.trim() === "") return true;
    return Number(raw) !== Number(saved);
  }
  return raw !== saved;
}

export function dirtyKeys(draft: Draft, settings: SettingsState): string[] {
  return FIELDS.filter((field) => isFieldDirty(field, draft, settings)).map((field) => field.key);
}

interface CrossRule {
  keys: string[];
  broken: (d: Draft) => boolean;
  message: string;
}

const n = (d: Draft, key: string) => Number(d[key]);

const CROSS_RULES: CrossRule[] = [
  {
    keys: ["stage0_min_ask", "stage0_max_ask"],
    broken: (d) => n(d, "stage0_min_ask") > n(d, "stage0_max_ask"),
    message: "Min ask must not be higher than the stage 0 max ask.",
  },
  {
    keys: ["stage0_mid_from_ask", "stage0_high_from_ask"],
    broken: (d) => n(d, "stage0_mid_from_ask") > n(d, "stage0_high_from_ask"),
    message: "Mid tier border must not be higher than the high tier border.",
  },
  {
    keys: ["stage1_sell_price_high", "stage1_sell_max_buy"],
    broken: (d) => n(d, "stage1_sell_price_high") <= n(d, "stage1_sell_max_buy"),
    message: "High sell price must be higher than the stage 1 cheap-buy limit.",
  },
  {
    keys: ["stage2_sell_price", "stage2_threshold"],
    broken: (d) => n(d, "stage2_sell_price") <= n(d, "stage2_threshold"),
    message: "Sell price must be higher than the stage 2 ask trigger.",
  },
  {
    keys: ["max_execution_price", "trigger_threshold", "stage2_threshold", "stage3_threshold"],
    broken: (d) =>
      n(d, "max_execution_price") <
      Math.max(n(d, "trigger_threshold"), n(d, "stage2_threshold"), n(d, "stage3_threshold")),
    message: "Must be at least the trigger and the stage 2/3 ask triggers.",
  },
];

/**
 * Field-level problems keyed by field key. Range and number errors are only reported for
 * fields the person has edited, so a stored value outside its range never blocks an
 * unrelated save. Cross-field rules are reported on their first key when any involved
 * field was edited.
 */
export function validateDraft(draft: Draft, settings: SettingsState): Record<string, string> {
  const errors: Record<string, string> = {};
  const dirty = new Set(dirtyKeys(draft, settings));

  for (const field of FIELDS) {
    if (field.kind !== "number" || !dirty.has(field.key)) continue;
    const raw = draft[field.key] ?? "";
    if (raw.trim() === "" || !Number.isFinite(Number(raw))) {
      errors[field.key] = "Enter a valid number.";
      continue;
    }
    const value = Number(raw);
    if (field.min !== undefined && value < Number(field.min)) {
      errors[field.key] = `Must be at least ${field.min}.`;
    } else if (field.max !== undefined && value > Number(field.max)) {
      errors[field.key] = `Must be at most ${field.max}.`;
    }
  }

  for (const rule of CROSS_RULES) {
    if (!rule.keys.some((key) => dirty.has(key))) continue;
    if (rule.keys.some((key) => draft[key] === undefined || !Number.isFinite(Number(draft[key])))) continue;
    if (rule.broken(draft) && !errors[rule.keys[0]]) errors[rule.keys[0]] = rule.message;
  }

  return errors;
}

export function buildDiff(draft: Draft, settings: SettingsState): Record<string, string | number> {
  const diff: Record<string, string | number> = {};
  for (const field of FIELDS) {
    if (!isFieldDirty(field, draft, settings)) continue;
    const raw = draft[field.key] ?? "";
    diff[field.key] = field.kind === "number" ? Number(raw) : raw;
  }
  return diff;
}
