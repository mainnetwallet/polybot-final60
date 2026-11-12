import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SettingsState } from "../api/types";
import { ModeBadge } from "../components/Badges";
import { IconAlert, IconCheck, IconClose, IconRefresh, IconSettings, IconShield, IconWallet } from "../components/Icons";
import { Modal } from "../components/Modal";
import { useBot } from "../hooks/BotContext";
import { cx, fmtUsd } from "../lib/format";

type FieldKind = "number" | "select";

interface FieldSpec {
  key: string;
  label: string;
  hint: string;
  kind: FieldKind;
  step?: string;
  min?: string;
  max?: string;
  options?: Array<{ value: string; label: string }>;
  suffix?: string;
}

const FIELDS: FieldSpec[] = [
  {
    key: "trigger_threshold",
    label: "Trigger threshold",
    hint: "Ask at or above this decimal fires a marketable BUY. It is never used as a limit price.",
    kind: "number",
    step: "0.01",
    min: "0.01",
    max: "0.999",
  },
  {
    key: "final_window_seconds",
    label: "Final window",
    hint: "Seconds before expiry in which trading is enabled.",
    kind: "number",
    step: "1",
    min: "1",
    max: "300",
    suffix: "s",
  },
  {
    key: "duplicate_policy",
    label: "Duplicate policy",
    hint: "Controls repeat entries for the same market or side.",
    kind: "select",
    options: [
      { value: "one_per_market", label: "One order per market" },
      { value: "one_per_side", label: "One per side" },
      { value: "allow", label: "Allow duplicates" },
    ],
  },
  {
    key: "conflict_policy",
    label: "Conflict policy",
    hint: "Deterministic decision when UP and DOWN both qualify.",
    kind: "select",
    options: [
      { value: "up_first", label: "UP first" },
      { value: "down_first", label: "DOWN first" },
      { value: "highest_ask", label: "Highest ask" },
      { value: "block_both", label: "Block both" },
    ],
  },
  {
    key: "trade_amount_usdc",
    label: "Order notional",
    hint: "USDC spent per marketable buy.",
    kind: "number",
    step: "0.5",
    min: "0.01",
    suffix: "USDC",
  },
  {
    key: "max_execution_price",
    label: "Max execution price",
    hint: "Hard ceiling for the live ask. Must be at least the trigger.",
    kind: "number",
    step: "0.01",
    min: "0.01",
    max: "1",
  },
  {
    key: "max_daily_loss",
    label: "Daily loss stop",
    hint: "Risk halt once the day's PnL falls to this loss.",
    kind: "number",
    step: "0.5",
    min: "0",
    suffix: "USDC",
  },
  {
    key: "daily_profit_target",
    label: "Daily profit target",
    hint: "Risk halt once the day's PnL reaches this profit.",
    kind: "number",
    step: "0.5",
    min: "0",
    suffix: "USDC",
  },
  {
    key: "max_trades_per_day",
    label: "Max trades / day",
    hint: "Hard cap on submitted entries per day.",
    kind: "number",
    step: "1",
    min: "1",
  },
  {
    key: "max_open_positions",
    label: "Max open positions",
    hint: "Maximum concurrent open positions in the active mode.",
    kind: "number",
    step: "1",
    min: "0",
  },
  {
    key: "stale_data_max_age_ms",
    label: "Stale data cap",
    hint: "Quotes older than this are rejected before order construction.",
    kind: "number",
    step: "100",
    min: "100",
    max: "60000",
    suffix: "ms",
  },
  {
    key: "paper_slippage_bps",
    label: "Paper slippage",
    hint: "Simulated adverse slippage applied to walked ask levels.",
    kind: "number",
    step: "1",
    min: "0",
    suffix: "bps",
  },
  {
    key: "paper_fee_rate",
    label: "Paper fee rate",
    hint: "Fee rate applied with the configured exponent in paper mode.",
    kind: "number",
    step: "0.001",
    min: "0",
    max: "1",
  },
];

function draftFrom(settings: SettingsState): Record<string, string> {
  const draft: Record<string, string> = {};
  for (const field of FIELDS) {
    draft[field.key] = String(settings[field.key as keyof SettingsState] ?? "");
  }
  return draft;
}

function Field({
  spec,
  value,
  onChange,
}: {
  spec: FieldSpec;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[11.5px] font-medium text-slate-300">{spec.label}</span>
        {spec.suffix ? <span className="mono text-[9px] uppercase tracking-[0.12em] text-slate-600">{spec.suffix}</span> : null}
      </div>
      {spec.kind === "select" ? (
        <select
          className="input mt-1.5 appearance-none pr-8"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          {spec.options?.map((option) => (
            <option key={option.value} value={option.value} className="bg-ink-900">
              {option.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          className="input mono mt-1.5"
          type="number"
          value={value}
          step={spec.step}
          min={spec.min}
          max={spec.max}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      <div className="mt-1.5 text-[10px] leading-relaxed text-slate-600">{spec.hint}</div>
    </label>
  );
}

function CheckRow({ ok, label, detail }: { ok: boolean; label: string; detail?: string }) {
  return (
    <div className="flex items-center gap-2 py-1.5">
      <span
        className={cx(
          "flex h-4 w-4 items-center justify-center rounded-full",
          ok ? "bg-up/15 text-up" : "bg-down/15 text-down",
        )}
      >
        {ok ? <IconCheck size={10} /> : <IconClose size={10} />}
      </span>
      <span className={cx("text-[11.5px]", ok ? "text-slate-300" : "text-slate-500")}>{label}</span>
      {detail ? <span className="mono ml-auto text-[10px] text-slate-600">{detail}</span> : null}
    </div>
  );
}

export function Settings() {
  const { snapshot, notify, resetPaper } = useBot();
  const [settings, setSettings] = useState<SettingsState | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetValue, setResetValue] = useState("");
  const [resetBusy, setResetBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .settings()
      .then((data) => {
        if (!alive) return;
        setSettings(data);
        setDraft(draftFrom(data));
        setResetValue(String(data.paper_starting_balance));
      })
      .catch((caught: unknown) => {
        if (alive) setError(caught instanceof Error ? caught.message : "Could not load settings");
      });
    return () => {
      alive = false;
    };
  }, []);

  const running = snapshot?.bot.status === "RUNNING";
  const readiness = snapshot?.live;

  const dirty =
    settings !== null &&
    FIELDS.some((field) => {
      const raw = draft[field.key] ?? "";
      if (field.kind === "select") return raw !== String(settings[field.key as keyof SettingsState]);
      return Number(raw) !== Number(settings[field.key as keyof SettingsState]);
    });

  const save = async () => {
    if (!settings) return;
    setError(null);
    const diff: Record<string, string | number> = {};
    for (const field of FIELDS) {
      const raw = draft[field.key] ?? "";
      if (field.kind === "select") {
        if (raw !== String(settings[field.key as keyof SettingsState])) diff[field.key] = raw;
        continue;
      }
      if (raw.trim() === "" || !Number.isFinite(Number(raw))) {
        setError(`${field.label} must be a number.`);
        return;
      }
      const value = Number(raw);
      if (value !== Number(settings[field.key as keyof SettingsState])) diff[field.key] = value;
    }
    if (!Object.keys(diff).length) {
      notify("info", "No changes", "Every field already matches the persisted runtime settings.");
      return;
    }
    if (Number(draft.max_execution_price) < Number(draft.trigger_threshold)) {
      setError("Max execution price must be greater than or equal to the trigger threshold.");
      return;
    }

    setSaving(true);
    try {
      const updated = await api.patchSettings(diff as Partial<SettingsState>);
      setSettings(updated);
      setDraft(draftFrom(updated));
      notify("success", "Settings saved", "Runtime overrides persisted to the local database.");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Settings update failed";
      setError(message);
      notify("error", "Settings rejected", message);
    } finally {
      setSaving(false);
    }
  };

  const validateLive = async () => {
    setValidating(true);
    try {
      const result = await api.validateLive();
      notify(
        "success",
        "Live connection verified",
        `Collateral ${fmtUsd(result.balance)}${result.allowance != null ? ` · allowance ${fmtUsd(result.allowance)}` : ""}`,
      );
    } catch (caught) {
      notify("error", "Live validation failed", caught instanceof Error ? caught.message : "Unknown error");
    } finally {
      setValidating(false);
    }
  };

  const confirmReset = async () => {
    const balance = Number(resetValue);
    if (!Number.isFinite(balance) || balance <= 0) return;
    setResetBusy(true);
    const result = await resetPaper(balance);
    setResetBusy(false);
    if (result.ok) setResetOpen(false);
  };

  if (!settings) {
    return (
      <div className="panel p-10 text-center">
        {error ? (
          <div className="text-[12px] text-down">{error}</div>
        ) : (
          <div className="text-[12px] text-slate-500">Loading runtime settings…</div>
        )}
      </div>
    );
  }

  const strategyFields = FIELDS.slice(0, 4);
  const riskFields = FIELDS.slice(4, 11);
  const paperFields = FIELDS.slice(11);

  return (
    <div className="space-y-4">
      <section className="panel flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/8 bg-white/[0.03] text-slate-400">
            <IconSettings size={17} />
          </div>
          <div>
            <div className="text-[13px] font-semibold text-slate-100">Runtime configuration</div>
            <div className="mt-0.5 text-[11px] text-slate-500">
              Validated overrides persist in bot_settings. Secrets and live enablement remain environment-only.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {dirty ? <span className="chip border-amber/30 bg-amber/10 text-amber">Unsaved changes</span> : null}
          <button
            type="button"
            className="btn btn-ghost"
            disabled={!dirty || saving}
            onClick={() => {
              setDraft(draftFrom(settings));
              setError(null);
            }}
          >
            <IconRefresh size={13} />
            Revert
          </button>
          <button type="button" className="btn btn-primary" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </section>

      {error ? (
        <div className="flex items-center gap-2 rounded-xl border border-down/25 bg-down/[0.07] px-4 py-3 text-[12px] text-down">
          <IconAlert size={14} />
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <section className="panel p-5 xl:col-span-6">
          <div className="label">Strategy</div>
          <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Entry logic</div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            {strategyFields.map((field) => (
              <Field
                key={field.key}
                spec={field}
                value={draft[field.key] ?? ""}
                onChange={(value) => setDraft((prev) => ({ ...prev, [field.key]: value }))}
              />
            ))}
          </div>
          <div className="mt-4 rounded-lg border border-sky/15 bg-sky/[0.04] px-3 py-2.5 text-[10.5px] leading-relaxed text-sky/80">
            The trigger is an activation threshold. The submitted price is always the live ask, capped by the max
            execution price. $0.80 is never placed as a limit.
          </div>
        </section>

        <section className="panel p-5 xl:col-span-6">
          <div className="label">Risk & execution</div>
          <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Guardrails</div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            {riskFields.map((field) => (
              <Field
                key={field.key}
                spec={field}
                value={draft[field.key] ?? ""}
                onChange={(value) => setDraft((prev) => ({ ...prev, [field.key]: value }))}
              />
            ))}
          </div>
        </section>

        <section className="panel p-5 xl:col-span-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="label">Paper simulation</div>
              <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Fill model</div>
            </div>
            <span className="chip border-sky/25 bg-sky/10 text-sky">Paper only</span>
          </div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            {paperFields.map((field) => (
              <Field
                key={field.key}
                spec={field}
                value={draft[field.key] ?? ""}
                onChange={(value) => setDraft((prev) => ({ ...prev, [field.key]: value }))}
              />
            ))}
            <div className="rounded-lg border hairline bg-black/20 p-3">
              <div className="label">Fill ratio</div>
              <div className="mono mt-1 text-[13px] text-slate-300">{settings.paper_fill_ratio}</div>
              <div className="mt-1 text-[10px] text-slate-600">Environment-only</div>
            </div>
            <div className="rounded-lg border hairline bg-black/20 p-3">
              <div className="label">Fee exponent</div>
              <div className="mono mt-1 text-[13px] text-slate-300">{settings.paper_fee_exponent}</div>
              <div className="mt-1 text-[10px] text-slate-600">Environment-only</div>
            </div>
          </div>
          <div className="mt-4 text-[10.5px] leading-relaxed text-slate-500">
            Paper fees use <span className="mono">shares × rate × (price × (1 − price))^exponent</span>. The fee
            formula is an approximation of Polymarket fees, not an exchange quote.
          </div>
        </section>

        <section className="panel p-5 xl:col-span-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="label">Live safety</div>
              <div className="mt-1 text-[14px] font-semibold tracking-tight text-slate-100">Environment gates</div>
            </div>
            {snapshot ? <ModeBadge mode={snapshot.bot.mode} /> : null}
          </div>
          <div className="mt-3 divide-y divide-white/[0.04]">
            <CheckRow ok={readiness?.live_enabled ?? false} label="LIVE_TRADING_ENABLED" detail=".env" />
            <CheckRow ok={readiness?.geo_verified ?? false} label="POLYMARKET_GEO_VERIFIED" detail=".env" />
            <CheckRow ok={readiness?.credentials_present ?? false} label="Signing key present" detail="backend only" />
            <CheckRow ok={readiness?.funder_configured ?? false} label="Funded wallet configured" detail=".env" />
          </div>
          <div className="mt-3 flex items-center justify-between">
            <span className="text-[10.5px] text-slate-500">
              {readiness?.balance != null ? `Collateral ${fmtUsd(readiness.balance)}` : "No live balance loaded"}
            </span>
            <button
              type="button"
              className="btn h-8 text-[11px]"
              disabled={!readiness?.ready || validating}
              onClick={() => void validateLive()}
            >
              <IconShield size={12} />
              {validating ? "Checking…" : "Validate live"}
            </button>
          </div>
          {!readiness?.ready ? (
            <div className="mt-3 rounded-lg border border-amber/20 bg-amber/[0.05] px-3 py-2 text-[10.5px] leading-relaxed text-amber/80">
              <div className="flex items-center gap-1.5 font-semibold">
                <IconAlert size={11} />
                Live gate blocked
              </div>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {(readiness?.reasons.length ? readiness.reasons : ["Live readiness unavailable."]).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="mt-3 flex items-center gap-1.5 text-[10px] text-slate-600">
            <IconAlert size={10} />
            Private keys are read by the backend only and never serialized to this dashboard.
          </div>
        </section>

        <section className="panel border-down/20 p-5 xl:col-span-12">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-down/20 bg-down/[0.07] text-down">
                <IconWallet size={16} />
              </div>
              <div>
                <div className="text-[13px] font-semibold text-slate-100">Reset paper account</div>
                <div className="mt-0.5 text-[11px] text-slate-500">
                  Clears paper orders, fills, positions and PnL, then recreates the paper balance. Live data is
                  untouched.
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-28">
                <input
                  className="input mono"
                  type="number"
                  step="0.5"
                  min="0.01"
                  value={resetValue}
                  onChange={(event) => setResetValue(event.target.value)}
                />
              </div>
              <button
                type="button"
                className="btn btn-danger"
                disabled={running}
                title={running ? "Stop the bot before resetting" : "Reset paper ledger"}
                onClick={() => setResetOpen(true)}
              >
                Reset paper
              </button>
            </div>
          </div>
          {running ? (
            <div className="mt-3 text-[10.5px] text-amber/80">Stop the bot before resetting the paper account.</div>
          ) : null}
        </section>
      </div>

      <Modal
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        eyebrow="Danger zone"
        title="Reset the paper ledger?"
        subtitle="This permanently deletes paper orders, fills, positions and PnL from the local database."
        danger
        footer={
          <>
            <button type="button" className="btn" onClick={() => setResetOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger"
              disabled={resetBusy || Number(resetValue) <= 0}
              onClick={() => void confirmReset()}
            >
              {resetBusy ? "Resetting…" : `Reset to ${fmtUsd(Number(resetValue))}`}
            </button>
          </>
        }
      >
        <div className="space-y-3 text-[12px] leading-relaxed text-slate-400">
          <p>
            The paper account will be recreated with{" "}
            <span className="mono text-slate-200">{fmtUsd(Number(resetValue))}</span> of virtual USDC. The bot must
            be stopped; live positions and balances are never touched.
          </p>
          <div className="flex items-center gap-2 rounded-lg border border-down/20 bg-down/[0.06] px-3 py-2 text-[11px] text-down/90">
            <IconAlert size={13} />
            This cannot be undone.
          </div>
        </div>
      </Modal>
    </div>
  );
}
