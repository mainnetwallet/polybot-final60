import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { SettingsState } from "../api/types";
import { ModeBadge, StatusPill } from "../components/Badges";
import { IconAlert, IconRefresh, IconSearch, IconSettings, IconShield, IconWallet } from "../components/Icons";
import { Modal } from "../components/Modal";
import { useBot } from "../hooks/BotContext";
import { cx, fmtUsd } from "../lib/format";
import { ActionRow, CheckRow, GroupCard, SaveBar, SettingRow, TabBar } from "./settings/components";
import { FIELDS } from "./settings/fields";
import { fieldsOf, TABS, tabOfField, type TabId } from "./settings/layout";
import { buildDiff, dirtyKeys, draftFrom, savedValue, validateDraft, type Draft } from "./settings/validate";

export function Settings() {
  const { snapshot, notify, resetPaper, resetDaily, resetServer } = useBot();
  const [settings, setSettings] = useState<SettingsState | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [tab, setTab] = useState<TabId>("entry");
  const [query, setQuery] = useState("");

  const [resetOpen, setResetOpen] = useState(false);
  const [resetValue, setResetValue] = useState("");
  const [resetBusy, setResetBusy] = useState(false);
  const [dailyOpen, setDailyOpen] = useState(false);
  const [dailyBusy, setDailyBusy] = useState(false);
  const [serverOpen, setServerOpen] = useState(false);
  const [serverPhrase, setServerPhrase] = useState("");
  const [serverBusy, setServerBusy] = useState(false);
  const [defaultsOpen, setDefaultsOpen] = useState(false);
  const [defaultsBusy, setDefaultsBusy] = useState(false);

  const applyLoaded = (data: SettingsState) => {
    setSettings(data);
    setDraft(draftFrom(data));
    setResetValue(String(data.paper_starting_balance));
  };

  useEffect(() => {
    let alive = true;
    api
      .settings()
      .then((data) => {
        if (alive) applyLoaded(data);
      })
      .catch((caught: unknown) => {
        if (alive) setLoadError(caught instanceof Error ? caught.message : "Could not load settings");
      });
    return () => {
      alive = false;
    };
  }, []);

  const running = snapshot?.bot.status === "RUNNING";
  const isLive = snapshot?.bot.mode === "live";
  const readiness = snapshot?.live;

  const dirty = useMemo(() => (settings ? new Set(dirtyKeys(draft, settings)) : new Set<string>()), [draft, settings]);
  const errors = useMemo(() => (settings ? validateDraft(draft, settings) : {}), [draft, settings]);
  const errorCount = Object.keys(errors).length;

  const countIn = (id: TabId, keys: Iterable<string>) => {
    let total = 0;
    for (const key of keys) if (tabOfField(key) === id) total += 1;
    return total;
  };

  const save = async () => {
    if (!settings || saving || dirty.size === 0) return;
    if (errorCount > 0) {
      setSaveError("Fix the highlighted fields before saving.");
      const firstTab = tabOfField(Object.keys(errors)[0]);
      if (firstTab) setTab(firstTab);
      return;
    }
    setSaveError(null);
    setSaving(true);
    try {
      const updated = await api.patchSettings(buildDiff(draft, settings) as Partial<SettingsState>);
      applyLoaded(updated);
      notify("success", "Settings saved", "Runtime overrides persisted to the local database.");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Settings update failed";
      setSaveError(message);
      notify("error", "Settings rejected", message);
    } finally {
      setSaving(false);
    }
  };

  const revertAll = () => {
    if (!settings) return;
    setDraft(draftFrom(settings));
    setSaveError(null);
  };

  // Ctrl/⌘ + S saves; leaving the page with unsaved edits asks for confirmation.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    if (dirty.size === 0) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty.size]);

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

  const confirmDefaultsReset = async () => {
    setDefaultsBusy(true);
    setSaveError(null);
    try {
      applyLoaded(await api.resetSettings());
      setDefaultsOpen(false);
      notify("success", "Settings reset", "All overrides removed. The .env defaults are active now (paper and live).");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Settings reset failed";
      setSaveError(message);
      notify("error", "Reset failed", message);
    } finally {
      setDefaultsBusy(false);
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

  const confirmDailyReset = async () => {
    setDailyBusy(true);
    const result = await resetDaily();
    setDailyBusy(false);
    if (result.ok) setDailyOpen(false);
  };

  const confirmServerReset = async () => {
    setServerBusy(true);
    const result = await resetServer(serverPhrase.trim());
    setServerBusy(false);
    if (result.ok) {
      setServerOpen(false);
      setServerPhrase("");
      try {
        applyLoaded(await api.settings());
      } catch {
        /* settings reload is best effort */
      }
    }
  };

  if (!settings) {
    return (
      <div className="panel p-10 text-center">
        {loadError ? (
          <div className="text-[12px] text-down">{loadError}</div>
        ) : (
          <div className="text-[12px] text-slate-500">Loading runtime settings…</div>
        )}
      </div>
    );
  }

  const renderRows = (keys: string[]) =>
    fieldsOf(keys).map((field) => (
      <SettingRow
        key={field.key}
        spec={field}
        value={draft[field.key] ?? ""}
        savedValue={savedValue(settings, field.key)}
        dirty={dirty.has(field.key)}
        error={errors[field.key]}
        onChange={(value) => setDraft((prev) => ({ ...prev, [field.key]: value }))}
      />
    ));

  const groupBadge = (keys: string[]) => {
    const edited = keys.filter((key) => dirty.has(key)).length;
    return edited ? (
      <span className="chip border-amber/30 bg-amber/10 text-amber">{edited} edited</span>
    ) : null;
  };

  const activeTab = TABS.find((item) => item.id === tab) ?? TABS[0];
  const needle = query.trim().toLowerCase();
  const searching = needle.length > 0;
  const matches = searching
    ? FIELDS.filter((field) => `${field.label} ${field.hint} ${field.key}`.toLowerCase().includes(needle))
    : [];

  const executionSection = (
    <div className="space-y-4">
      {!isLive ? (
        <GroupCard
          title="Paper fill model"
          description="Simulated fills used while the bot runs in paper mode."
          badge={<span className="chip border-sky/25 bg-sky/10 text-sky">Paper only</span>}
        >
          {renderRows(["paper_slippage_bps", "paper_fee_rate"])}
          <div className="grid gap-3 px-5 py-4 sm:grid-cols-2">
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
          <div className="px-5 py-3 text-[10.5px] leading-relaxed text-slate-500">
            Paper fees use <span className="mono">shares × rate × (price × (1 − price))^exponent</span>. The formula is
            an approximation of Polymarket fees, not an exchange quote.
          </div>
        </GroupCard>
      ) : (
        <GroupCard
          title="Live execution"
          description="Real fills on Polymarket."
          badge={<span className="chip border-down/25 bg-down/10 text-down">Live mode</span>}
        >
          <div className="px-5 py-4 text-[11px] leading-relaxed text-slate-500">
            Live orders are filled by Polymarket, so the paper slippage and fee simulation do not apply. Fees come from
            the market&apos;s own CLOB parameters. Strategy, exit and risk settings apply to live orders exactly as they
            do in paper mode. Switch to paper mode to edit the paper fill model.
          </div>
        </GroupCard>
      )}

      <GroupCard
        title="Live safety gates"
        description="Environment-controlled checks that must pass before live trading can start."
        badge={snapshot ? <ModeBadge mode={snapshot.bot.mode} /> : null}
      >
        <CheckRow ok={readiness?.live_enabled ?? false} label="LIVE_TRADING_ENABLED" detail=".env" />
        <CheckRow ok={readiness?.geo_verified ?? false} label="POLYMARKET_GEO_VERIFIED" detail=".env" />
        <CheckRow ok={readiness?.credentials_present ?? false} label="Signing key present" detail="backend only" />
        <CheckRow ok={readiness?.funder_configured ?? false} label="Funded wallet configured" detail=".env" />
        <div className="flex items-center justify-between gap-3 px-5 py-3.5">
          <span className="text-[11px] text-slate-500">
            {readiness?.balance != null ? `Collateral ${fmtUsd(readiness.balance)}` : "No live balance loaded"}
          </span>
          <button
            type="button"
            className="btn h-8 text-[11px]"
            disabled={!readiness?.ready || validating}
            onClick={() => void validateLive()}
          >
            <IconShield size={12} />
            {validating ? "Checking…" : "Validate live connection"}
          </button>
        </div>
        {!readiness?.ready ? (
          <div className="bg-amber/[0.04] px-5 py-3.5 text-[10.5px] leading-relaxed text-amber/85">
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
        <div className="flex items-center gap-1.5 px-5 py-3 text-[10px] text-slate-600">
          <IconAlert size={10} />
          Private keys are read by the backend only and never serialized to this dashboard.
        </div>
      </GroupCard>
    </div>
  );

  const maintenanceSection = (
    <div className="space-y-4">
      <GroupCard title="Daily counters" description="Safe to use while the bot is running.">
        <ActionRow
          tone="amber"
          icon={<IconAlert size={16} />}
          title="Reset daily trades & profit/loss"
          description="Sets today's trade count and daily profit/loss back to zero for the current mode, so the daily loss stop, profit target and max-trades limits start fresh. Order history, positions and balance are kept."
        >
          <button type="button" className="btn btn-danger" onClick={() => setDailyOpen(true)}>
            Reset daily
          </button>
        </ActionRow>
      </GroupCard>

      {!isLive ? (
        <GroupCard title="Paper account" description="Only affects the simulated ledger.">
          <ActionRow
            tone="down"
            icon={<IconWallet size={16} />}
            title="Reset paper account"
            description="Clears paper orders, fills, positions and PnL, then recreates the paper balance. Live data is untouched."
            note={running ? "Stop the bot before resetting the paper account." : undefined}
          >
            <div className="w-28">
              <input
                className="input mono"
                type="number"
                step="0.5"
                min="0.01"
                aria-label="Paper starting balance"
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
          </ActionRow>
        </GroupCard>
      ) : null}

      <GroupCard title="Settings defaults" description="Discard every saved dashboard override.">
        <ActionRow
          tone="amber"
          icon={<IconRefresh size={16} />}
          title="Reset settings to .env defaults"
          description="Deletes the saved overrides so the values from .env take over immediately, even while the bot is running. Orders, positions, balance and history are not touched."
        >
          <button type="button" className="btn btn-danger" disabled={saving} onClick={() => setDefaultsOpen(true)}>
            Reset to defaults
          </button>
        </ActionRow>
      </GroupCard>

      <section className="panel overflow-hidden border-down/30">
        <header className="border-b border-down/20 bg-down/[0.05] px-5 py-3">
          <h3 className="text-[13px] font-semibold tracking-tight text-down">Danger zone</h3>
        </header>
        <ActionRow
          tone="down"
          icon={<IconAlert size={16} />}
          title="Full server reset"
          description={
            <>
              Wipes the whole database: orders, fills, positions, markets, price history, triggers, logs and dashboard
              setting overrides. Mode returns to paper, the bot stays stopped and settings go back to the{" "}
              <span className="mono">.env</span> defaults.
            </>
          }
          note={running ? "Stop the bot before resetting the server." : undefined}
        >
          <button
            type="button"
            className="btn btn-danger"
            disabled={running}
            title={running ? "Stop the bot before resetting" : "Wipe all data"}
            onClick={() => setServerOpen(true)}
          >
            Reset everything
          </button>
        </ActionRow>
      </section>
    </div>
  );

  return (
    <div className="mx-auto max-w-[1100px]">
      <section className="panel p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/8 bg-white/[0.03] text-slate-400">
              <IconSettings size={18} />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-[15px] font-semibold tracking-tight text-slate-100">Runtime configuration</h2>
                {snapshot ? <ModeBadge mode={snapshot.bot.mode} size="sm" /> : null}
                <StatusPill running={snapshot ? running : null} killSwitch={snapshot?.bot.kill_switch ?? null} />
              </div>
              <p className="mt-1 max-w-[62ch] text-[11px] leading-relaxed text-slate-500">
                Saved changes apply immediately to the running bot in both paper and live mode. Secrets and live
                enablement remain environment-only.
              </p>
            </div>
          </div>

          <div className="relative w-full lg:w-72">
            <IconSearch size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
            <input
              className="input !pl-9"
              type="search"
              placeholder="Search settings…"
              aria-label="Search settings"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>

        {running && !searching ? (
          <div className="mt-4 flex items-center gap-2 rounded-lg border border-amber/20 bg-amber/[0.05] px-3 py-2 text-[11px] text-amber/90">
            <IconAlert size={12} />
            The bot is running. Saved strategy and risk changes take effect on the next evaluation.
          </div>
        ) : null}
      </section>

      {saveError ? (
        <div className="mt-4 flex items-center gap-2 rounded-xl border border-down/25 bg-down/[0.07] px-4 py-3 text-[12px] text-down">
          <IconAlert size={14} />
          {saveError}
        </div>
      ) : null}

      {!searching ? (
        <div className="mt-4">
          <TabBar
            tabs={TABS}
            active={tab}
            onChange={setTab}
            editedCount={(id) => countIn(id, dirty)}
            errorCount={(id) => countIn(id, Object.keys(errors))}
          />
        </div>
      ) : null}

      <div className="mt-4 space-y-4">
        {searching ? (
          <>
            <div className="px-1 text-[11px] text-slate-500">
              {matches.length} {matches.length === 1 ? "setting matches" : "settings match"}{" "}
              <span className="mono text-slate-300">&ldquo;{query.trim()}&rdquo;</span>
            </div>
            {matches.length === 0 ? (
              <div className="panel p-10 text-center text-[12px] text-slate-500">No settings found.</div>
            ) : (
              TABS.map((item) => {
                const keys = item.groups.flatMap((group) => group.keys).filter((key) => matches.some((m) => m.key === key));
                if (!keys.length) return null;
                return (
                  <GroupCard key={item.id} title={item.title} description={item.description} badge={groupBadge(keys)}>
                    {renderRows(keys)}
                  </GroupCard>
                );
              })
            )}
          </>
        ) : activeTab.id === "execution" ? (
          executionSection
        ) : activeTab.id === "maintenance" ? (
          maintenanceSection
        ) : (
          <>
            <div className="px-1">
              <h2 className="text-[14px] font-semibold tracking-tight text-slate-100">{activeTab.title}</h2>
              <p className="mt-0.5 text-[11.5px] text-slate-500">{activeTab.description}</p>
            </div>
            {activeTab.groups.map((group) => (
              <GroupCard key={group.title} title={group.title} description={group.description} badge={groupBadge(group.keys)}>
                {renderRows(group.keys)}
              </GroupCard>
            ))}
            {activeTab.id === "entry" ? (
              <div className="rounded-xl border border-sky/15 bg-sky/[0.04] px-4 py-3 text-[11px] leading-relaxed text-sky/80">
                The trigger is an activation threshold. The submitted price is always the live ask, capped by the max
                execution price
                {snapshot ? ` (trigger currently $${snapshot.strategy.trigger_threshold.toFixed(2)})` : ""}. It is never
                placed as a limit.
              </div>
            ) : null}
          </>
        )}
      </div>

      <SaveBar count={dirty.size} errors={errorCount} saving={saving} onSave={() => void save()} onRevert={revertAll} />

      <Modal
        open={defaultsOpen}
        onClose={() => setDefaultsOpen(false)}
        eyebrow="Settings"
        title="Reset settings to .env defaults?"
        subtitle="Every value you changed on this page is discarded."
        footer={
          <>
            <button type="button" className="btn" onClick={() => setDefaultsOpen(false)}>
              Cancel
            </button>
            <button type="button" className="btn btn-danger" disabled={defaultsBusy} onClick={() => void confirmDefaultsReset()}>
              {defaultsBusy ? "Resetting…" : "Reset to defaults"}
            </button>
          </>
        }
      >
        <div className="space-y-3 text-[12px] leading-relaxed text-slate-400">
          <p>
            The saved overrides are deleted and the values from <span className="mono">.env</span> take over right away,
            even while the bot is running. Orders, positions, balance and history are not touched.
          </p>
          <div className="flex items-center gap-2 rounded-lg border border-amber/20 bg-amber/[0.06] px-3 py-2 text-[11px] text-amber/90">
            <IconAlert size={13} />
            Strategy and risk limits change immediately for the active mode.
          </div>
        </div>
      </Modal>

      <Modal
        open={dailyOpen}
        onClose={() => setDailyOpen(false)}
        eyebrow="Daily counters"
        title="Reset daily trades and profit/loss?"
        subtitle="Today's counters restart from zero. No history is deleted."
        footer={
          <>
            <button type="button" className="btn" onClick={() => setDailyOpen(false)}>
              Cancel
            </button>
            <button type="button" className="btn btn-danger" disabled={dailyBusy} onClick={() => void confirmDailyReset()}>
              {dailyBusy ? "Resetting…" : "Reset daily"}
            </button>
          </>
        }
      >
        <div className="space-y-3 text-[12px] leading-relaxed text-slate-400">
          <p>
            This clears the daily trade count and daily realized profit/loss for the{" "}
            <span className="mono text-slate-200">{snapshot?.bot.mode ?? "current"}</span> mode and lifts any daily
            loss/profit halt. It can be used while the bot is running.
          </p>
          <div className="flex items-center gap-2 rounded-lg border border-amber/20 bg-amber/[0.06] px-3 py-2 text-[11px] text-amber/90">
            <IconAlert size={13} />
            Risk limits will allow new trades again after this reset.
          </div>
        </div>
      </Modal>

      <Modal
        open={serverOpen}
        onClose={() => {
          setServerOpen(false);
          setServerPhrase("");
        }}
        eyebrow="Danger zone"
        title="Reset the entire server?"
        subtitle="This permanently deletes all data in the local database."
        danger
        footer={
          <>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setServerOpen(false);
                setServerPhrase("");
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger"
              disabled={serverBusy || serverPhrase.trim() !== "RESET ALL"}
              onClick={() => void confirmServerReset()}
            >
              {serverBusy ? "Resetting…" : "Wipe everything"}
            </button>
          </>
        }
      >
        <div className="space-y-3 text-[12px] leading-relaxed text-slate-400">
          <p>
            Paper and live records, logs, price history and setting overrides are deleted. The bot returns to paper
            mode, stopped, with settings from <span className="mono">.env</span>. Real exchange balances are never
            touched, and the reset is refused while live positions or orders are still open.
          </p>
          <div>
            <label className="mb-1 block text-[11px] text-slate-500">
              Type <span className="mono text-slate-200">RESET ALL</span> to confirm
            </label>
            <input
              className="input mono"
              value={serverPhrase}
              onChange={(event) => setServerPhrase(event.target.value)}
              placeholder="RESET ALL"
              autoComplete="off"
            />
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-down/20 bg-down/[0.06] px-3 py-2 text-[11px] text-down/90">
            <IconAlert size={13} />
            This cannot be undone.
          </div>
        </div>
      </Modal>

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
              className={cx("btn btn-danger")}
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
            <span className="mono text-slate-200">{fmtUsd(Number(resetValue))}</span> of virtual USDC. The bot must be
            stopped; live positions and balances are never touched.
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
