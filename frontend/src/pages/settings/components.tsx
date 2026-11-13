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
  return (
    <div
      className={cx(
        "grid gap-x-6 gap-y-2 px-5 py-4 md:grid-cols-[minmax(0,1fr)_220px]",
        dirty && "bg-amber/[0.035]",
      )}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <label htmlFor={id} className="text-[12.5px] font-medium text-slate-200">
            {spec.label}
          </label>
          {dirty ? (
            <span className="chip border-amber/30 bg-amber/10 !px-1.5 !py-px text-[8.5px] text-amber">Edited</span>
          ) : null}
        </div>
        <p className="mt-1 max-w-[62ch] text-[11px] leading-relaxed text-slate-500">{spec.hint}</p>
        <div className="mono mt-1 text-[9.5px] text-slate-700">{spec.key}</div>
      </div>

      <div className="md:pt-0.5">
        {spec.kind === "toggle" ? (
          <button
            id={id}
            type="button"
            role="switch"
            aria-checked={value === "true"}
            onClick={() => onChange(value === "true" ? "false" : "true")}
            className="flex items-center gap-3 md:ml-auto"
          >
            <span
              className={cx(
                "relative h-6 w-11 rounded-full border transition-colors",
                value === "true" ? "border-up/40 bg-up/25" : "border-white/10 bg-white/[0.04]",
              )}
            >
              <span
                className={cx(
                  "absolute top-0.5 h-[18px] w-[18px] rounded-full transition-all",
                  value === "true" ? "left-[22px] bg-up" : "left-0.5 bg-slate-500",
                )}
              />
            </span>
            <span className={cx("w-7 text-[11px] font-semibold", value === "true" ? "text-up" : "text-slate-500")}>
              {value === "true" ? "On" : "Off"}
            </span>
          </button>
        ) : spec.kind === "select" ? (
          <select
            id={id}
            className="input select-input"
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
              className={cx("input mono", spec.suffix && "pr-14")}
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
              <span className="mono pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[9.5px] uppercase tracking-[0.12em] text-slate-600">
                {spec.suffix}
              </span>
            ) : null}
          </div>
        )}

        {error ? (
          <div className="mt-1.5 flex items-start gap-1.5 text-[10.5px] leading-snug text-down">
            <IconAlert size={11} className="mt-px shrink-0" />
            {error}
          </div>
        ) : dirty ? (
          <button
            type="button"
            onClick={() => onChange(savedValue)}
            className="mt-1.5 flex items-center gap-1 text-[10.5px] text-slate-500 hover:text-slate-300 md:ml-auto"
          >
            <IconRefresh size={10} />
            Saved: <span className="mono">{spec.kind === "toggle" ? (savedValue === "true" ? "On" : "Off") : savedValue}</span>
          </button>
        ) : null}
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
    <section className="panel overflow-hidden">
      <header className="flex items-start justify-between gap-4 border-b hairline px-5 py-3.5">
        <div>
          <h3 className="text-[13px] font-semibold tracking-tight text-slate-100">{title}</h3>
          {description ? <p className="mt-0.5 text-[11px] text-slate-500">{description}</p> : null}
        </div>
        {badge}
      </header>
      <div className="divide-y divide-white/[0.05]">{children}</div>
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
    <div role="tablist" className="scrollbar flex gap-1 overflow-x-auto rounded-xl border hairline bg-black/20 p-1">
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
              "flex shrink-0 items-center gap-2 rounded-lg px-3.5 py-2 text-[12px] font-medium transition-colors",
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

export function SaveBar({
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
  if (count === 0) return null;
  return (
    <div className="pointer-events-none sticky bottom-4 z-20 mt-6 flex justify-center">
      <div className="pointer-events-auto flex w-full max-w-[640px] items-center gap-3 rounded-2xl border border-white/10 bg-ink-900/95 px-4 py-3 shadow-[0_20px_50px_-12px_rgba(0,0,0,0.9)] backdrop-blur-xl">
        <span className="live-dot h-2 w-2 shrink-0 rounded-full bg-amber" />
        <div className="min-w-0 flex-1">
          <div className="text-[12.5px] font-semibold text-slate-100">
            {count} unsaved {count === 1 ? "change" : "changes"}
          </div>
          <div className={cx("truncate text-[10.5px]", errors ? "text-down" : "text-slate-500")}>
            {errors
              ? `Fix ${errors} ${errors === 1 ? "field" : "fields"} before saving`
              : "Applies immediately to the running bot · Ctrl/⌘ + S"}
          </div>
        </div>
        <button type="button" className="btn" disabled={saving} onClick={onRevert}>
          <IconClose size={12} />
          Discard
        </button>
        <button type="button" className="btn btn-primary" disabled={saving || errors > 0} onClick={onSave}>
          <IconCheck size={13} />
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
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
    <div className="px-5 py-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <div
            className={cx(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border",
              tone === "amber" ? "border-amber/20 bg-amber/[0.07] text-amber" : "border-down/25 bg-down/[0.08] text-down",
            )}
          >
            {icon}
          </div>
          <div>
            <div className="text-[13px] font-semibold text-slate-100">{title}</div>
            <div className="mt-0.5 max-w-[68ch] text-[11px] leading-relaxed text-slate-500">{description}</div>
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
    <div className="flex items-center gap-2.5 px-5 py-3">
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
