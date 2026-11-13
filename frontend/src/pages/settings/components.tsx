import type { ReactNode } from "react";
import { IconAlert, IconCheck, IconClose, IconRefresh } from "../../components/Icons";
import { cx } from "../../lib/format";
import type { FieldSpec } from "./fields";
import type { TabId, TabSpec } from "./layout";

export function SettingRow({
  spec,
  value,
  savedValue,
  dirty,
  error,
  onChange,
}: {
  spec: FieldSpec;
  value: string;
  savedValue: string;
  dirty: boolean;
  error?: string;
  onChange: (value: string) => void;
}) {
  const id = `setting-${spec.key}`;
  const saved = spec.kind === "toggle" ? (savedValue === "true" ? "On" : "Off") : savedValue;
  return (
    <div
      className={cx(
        "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 px-4 py-2.5 transition-colors",
        dirty && "bg-amber/[0.04] shadow-[inset_2px_0_0_0_rgba(245,181,68,0.7)]",
        error && "bg-down/[0.04] shadow-[inset_2px_0_0_0_rgba(255,95,126,0.7)]",
      )}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <label htmlFor={id} title={spec.key} className="truncate text-[12.5px] font-medium text-slate-200">
            {spec.label}
          </label>
          {dirty ? (
            <button
              type="button"
              onClick={() => onChange(savedValue)}
              title={`Revert to saved value: ${saved}`}
              className="inline-flex shrink-0 items-center gap-1 rounded-full border border-amber/30 bg-amber/10 px-1.5 py-px text-[9px] font-bold uppercase tracking-wider text-amber hover:bg-amber/20"
            >
              <IconRefresh size={9} />
              Revert
            </button>
          ) : null}
        </div>
        {error ? (
          <div className="mt-0.5 flex items-start gap-1 text-[10.5px] leading-snug text-down">
            <IconAlert size={11} className="mt-px shrink-0" />
            {error}
          </div>
        ) : (
          <p className="mt-0.5 text-[10.5px] leading-snug text-slate-500 sm:truncate" title={spec.hint}>
            {spec.hint}
          </p>
        )}
      </div>

      <div className="w-[150px] sm:w-[170px]">
        {spec.kind === "toggle" ? (
          <button
            id={id}
            type="button"
            role="switch"
            aria-checked={value === "true"}
            onClick={() => onChange(value === "true" ? "false" : "true")}
            className="ml-auto flex items-center gap-2.5"
          >
            <span className={cx("w-6 text-right text-[11px] font-semibold", value === "true" ? "text-up" : "text-slate-500")}>
              {value === "true" ? "On" : "Off"}
            </span>
            <span
              className={cx(
                "relative h-5 w-9 rounded-full border transition-colors",
                value === "true" ? "border-up/40 bg-up/25" : "border-white/10 bg-white/[0.04]",
              )}
            >
              <span
                className={cx(
                  "absolute top-[2px] h-3.5 w-3.5 rounded-full transition-all",
                  value === "true" ? "left-[18px] bg-up" : "left-[2px] bg-slate-500",
                )}
              />
            </span>
          </button>
        ) : spec.kind === "select" ? (
          <select
            id={id}
            className="input input-sm select-input"
            value={value}
            aria-invalid={!!error}
            onChange={(event) => onChange(event.target.value)}
          >
            {spec.options?.map((option) => (
              <option key={option.value} value={option.value} className="bg-ink-900">
                {option.label}
              </option>
            ))}
          </select>
        ) : (
          <div className="relative">
            <input
              id={id}
              className={cx("input input-sm mono", spec.suffix && "pr-12")}
              type="number"
              inputMode="decimal"
              value={value}
              step={spec.step}
              min={spec.min}
              max={spec.max}
              aria-invalid={!!error}
              onChange={(event) => onChange(event.target.value)}
            />
            {spec.suffix ? (
              <span className="mono pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[9px] uppercase tracking-[0.1em] text-slate-600">
                {spec.suffix}
              </span>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

export function GroupCard({
  title,
  description,
  badge,
  children,
}: {
  title: string;
  description?: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel mb-4 break-inside-avoid overflow-hidden !rounded-xl">
      <header className="flex items-start justify-between gap-3 border-b hairline bg-white/[0.015] px-4 py-2.5">
        <div className="min-w-0">
          <h3 className="text-[12.5px] font-semibold tracking-tight text-slate-100">{title}</h3>
          {description ? <p className="mt-0.5 text-[10.5px] leading-snug text-slate-500">{description}</p> : null}
        </div>
        {badge}
      </header>
      <div className="divide-y divide-white/[0.045]">{children}</div>
    </section>
  );
}

export function TabBar({
  tabs,
  active,
  onChange,
  editedCount,
  errorCount,
}: {
  tabs: TabSpec[];
  active: TabId;
  onChange: (id: TabId) => void;
  editedCount: (id: TabId) => number;
  errorCount: (id: TabId) => number;
}) {
  return (
    <div role="tablist" className="scrollbar flex gap-0.5 overflow-x-auto px-2 py-1.5">
      {tabs.map((tab) => {
        const isActive = tab.id === active;
        const edited = editedCount(tab.id);
        const errors = errorCount(tab.id);
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(tab.id)}
            className={cx(
              "flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors",
              isActive ? "bg-white/[0.08] text-slate-100" : "text-slate-500 hover:bg-white/[0.03] hover:text-slate-300",
              tab.id === "maintenance" && !isActive && "hover:text-down/80",
            )}
          >
            {tab.label}
            {errors > 0 ? (
              <span className="mono rounded-full bg-down/15 px-1.5 text-[9.5px] font-bold text-down">{errors}</span>
            ) : edited > 0 ? (
              <span className="mono rounded-full bg-amber/15 px-1.5 text-[9.5px] font-bold text-amber">{edited}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function SaveControls({
  count,
  errors,
  saving,
  onSave,
  onRevert,
}: {
  count: number;
  errors: number;
  saving: boolean;
  onSave: () => void;
  onRevert: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      {count > 0 ? (
        <span
          className={cx(
            "mr-1 hidden items-center gap-1.5 text-[11px] font-medium md:flex",
            errors ? "text-down" : "text-amber",
          )}
        >
          <span className={cx("h-1.5 w-1.5 rounded-full", errors ? "bg-down" : "live-dot bg-amber")} />
          {errors ? `${errors} ${errors === 1 ? "error" : "errors"}` : `${count} unsaved`}
        </span>
      ) : null}
      <button type="button" className="btn !px-3 !py-1.5 !text-[12px]" disabled={saving || count === 0} onClick={onRevert}>
        <IconClose size={11} />
        Discard
      </button>
      <button
        type="button"
        className="btn btn-primary !px-3.5 !py-1.5 !text-[12px]"
        disabled={saving || count === 0 || errors > 0}
        onClick={onSave}
        title="Ctrl/⌘ + S"
      >
        <IconCheck size={12} />
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

export function ActionRow({
  icon,
  title,
  description,
  tone,
  children,
  note,
}: {
  icon: ReactNode;
  title: string;
  description: ReactNode;
  tone: "amber" | "down";
  children: ReactNode;
  note?: string;
}) {
  return (
    <div className="px-4 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <div
            className={cx(
              "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border",
              tone === "amber" ? "border-amber/20 bg-amber/[0.07] text-amber" : "border-down/25 bg-down/[0.08] text-down",
            )}
          >
            {icon}
          </div>
          <div>
            <div className="text-[12.5px] font-semibold text-slate-100">{title}</div>
            <div className="mt-0.5 max-w-[68ch] text-[10.5px] leading-relaxed text-slate-500">{description}</div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">{children}</div>
      </div>
      {note ? <div className="mt-3 text-[10.5px] text-amber/80">{note}</div> : null}
    </div>
  );
}

export function CheckRow({ ok, label, detail }: { ok: boolean; label: string; detail?: string }) {
  return (
    <div className="flex items-center gap-2.5 px-4 py-2">
      <span
        className={cx(
          "flex h-4 w-4 items-center justify-center rounded-full",
          ok ? "bg-up/15 text-up" : "bg-down/15 text-down",
        )}
      >
        {ok ? <IconCheck size={10} /> : <IconClose size={10} />}
      </span>
      <span className={cx("mono text-[11.5px]", ok ? "text-slate-300" : "text-slate-500")}>{label}</span>
      {detail ? <span className="mono ml-auto text-[10px] text-slate-600">{detail}</span> : null}
    </div>
  );
}
