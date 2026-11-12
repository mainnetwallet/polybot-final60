import { useEffect, useState, type ReactNode } from "react";
import type { Mode } from "../api/types";
import { useBot } from "../hooks/BotContext";
import { cx } from "../lib/format";
import { IconAlert, IconCheck, IconClose, IconLock, IconShield, IconZap } from "./Icons";
import { Modal } from "./Modal";

function ReadinessRow({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border hairline bg-black/20 px-2.5 py-2">
      <span
        className={cx(
          "flex h-4 w-4 items-center justify-center rounded-full",
          ok ? "bg-up/15 text-up" : "bg-down/15 text-down",
        )}
      >
        {ok ? <IconCheck size={10} /> : <IconClose size={10} />}
      </span>
      <span className={cx("text-[11.5px]", ok ? "text-slate-300" : "text-slate-500")}>{label}</span>
    </div>
  );
}

function Warning({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-2.5 rounded-lg border border-amber/25 bg-amber/[0.07] px-3 py-2.5 text-[11.5px] leading-relaxed text-amber/90">
      <IconAlert size={14} className="mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

export function ModeSwitchModal({
  open,
  onClose,
  target,
}: {
  open: boolean;
  onClose: () => void;
  target: Mode;
}) {
  const { snapshot, switchMode } = useBot();
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setPhrase("");
      setError(null);
      setBusy(false);
    }
  }, [open, target]);

  const running = snapshot?.bot.status === "RUNNING";
  const outstanding = snapshot ? snapshot.open_orders.length : null;
  const readiness = snapshot?.live;
  const live = target === "live";
  const phraseOk = !live || phrase.trim().length > 0;
  const canSwitch = snapshot !== null && !busy && !running && outstanding === 0 && (!live || (readiness?.ready === true && phraseOk));

  const confirm = async () => {
    setBusy(true);
    setError(null);
    const result = await switchMode(target, live ? phrase.trim() : "");
    setBusy(false);
    if (result.ok) onClose();
    else setError(result.error ?? "Mode switch rejected");
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Execution mode"
      title={live ? "Switch to LIVE trading" : "Switch to PAPER trading"}
      subtitle={
        live
          ? "The live engine submits real marketable orders to Polymarket using funds in the configured wallet."
          : "Paper mode uses the live Polymarket feed but simulates every fill. No real orders are sent."
      }
      danger={live}
      footer={
        <>
          <button type="button" onClick={onClose} className="btn">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={!canSwitch}
            className={cx("btn", live ? "btn-danger" : "btn-primary")}
          >
            {busy ? "Switching…" : live ? "Switch to LIVE" : "Switch to PAPER"}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {live ? (
          <>
            <Warning>
              Live mode sends real orders. Keys stay in the backend environment and are never sent to this
              dashboard. There is no automatic switch from paper to live.
            </Warning>
            <div className="grid grid-cols-2 gap-2">
              <ReadinessRow ok={readiness?.live_enabled ?? false} label="LIVE_TRADING_ENABLED" />
              <ReadinessRow ok={readiness?.geo_verified ?? false} label="Geo eligibility verified" />
              <ReadinessRow ok={readiness?.credentials_present ?? false} label="Signing key present" />
              <ReadinessRow ok={readiness?.funder_configured ?? false} label="Funded wallet configured" />
            </div>
            {!readiness?.ready ? (
              <div className="rounded-lg border border-down/20 bg-down/[0.06] px-3 py-2.5">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-down/80">
                  Live gate blocked
                </div>
                <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[11.5px] text-slate-400">
                  {(readiness?.reasons.length ? readiness.reasons : ["Live readiness has not been confirmed."]).map(
                    (reason) => (
                      <li key={reason}>{reason}</li>
                    ),
                  )}
                </ul>
              </div>
            ) : null}
            <div>
              <label className="label mb-1.5 block" htmlFor="mode-live-phrase">
                Type the configured confirmation phrase to confirm
              </label>
              <input
                id="mode-live-phrase"
                className="input mono uppercase tracking-[0.2em]"
                value={phrase}
                onChange={(event) => setPhrase(event.target.value)}
                placeholder="Confirmation phrase"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          </>
        ) : (
          <div className="flex gap-2.5 rounded-lg border border-sky/20 bg-sky/[0.06] px-3 py-2.5 text-[11.5px] leading-relaxed text-sky/90">
            <IconLock size={14} className="mt-0.5 shrink-0" />
            <div>
              Paper execution is completely isolated from live balances and statistics. Existing paper orders and
              positions stay in the paper ledger.
            </div>
          </div>
        )}

        {running ? (
          <Warning>Stop the bot before switching execution mode. The backend rejects mode changes while running.</Warning>
        ) : null}
        {outstanding !== null && outstanding > 0 ? (
          <Warning>
            {outstanding} outstanding order{outstanding === 1 ? "" : "s"} must be resolved before the mode can change.
          </Warning>
        ) : null}

        {error ? (
          <div className="rounded-lg border border-down/30 bg-down/10 px-3 py-2 text-[11.5px] text-down">{error}</div>
        ) : null}
      </div>
    </Modal>
  );
}

export function StartLiveModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { snapshot, start } = useBot();
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readiness = snapshot?.live;

  useEffect(() => {
    if (open) {
      setPhrase("");
      setError(null);
      setBusy(false);
    }
  }, [open]);

  const canStart = !busy && readiness?.ready === true && phrase.trim().length > 0;

  const confirm = async () => {
    setBusy(true);
    setError(null);
    const result = await start(phrase.trim());
    setBusy(false);
    if (result.ok) onClose();
    else setError(result.error ?? "Live start rejected");
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Live execution"
      title="Start the LIVE engine"
      subtitle={`The engine will submit real FOK marketable buys when an ask reaches the trigger inside the final window${snapshot ? ` (${snapshot.strategy.final_window_seconds} seconds)` : ""}.`}
      danger
      footer={
        <>
          <button type="button" onClick={onClose} className="btn">
            Cancel
          </button>
          <button type="button" onClick={() => void confirm()} disabled={!canStart} className="btn btn-danger">
            {busy ? "Starting…" : "Start live engine"}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <Warning>
          Real funds are at risk. Orders are marketable (FOK) at the current ask, never limited to the trigger price. The
          kill switch stops execution immediately.
        </Warning>
        <div className="flex items-center justify-between rounded-lg border hairline bg-black/20 px-3 py-2.5">
          <span className="flex items-center gap-2 text-[11.5px] text-slate-400">
            <IconShield size={13} className="text-slate-500" />
            On-chain collateral available
          </span>
          <span className="mono text-[12.5px] text-slate-200">
            {readiness?.balance != null ? `$${readiness.balance.toFixed(2)}` : "—"}
          </span>
        </div>
        <div>
          <label className="label mb-1.5 block" htmlFor="start-live-phrase">
            Type the configured confirmation phrase to confirm
          </label>
          <input
            id="start-live-phrase"
            className="input mono uppercase tracking-[0.2em]"
            value={phrase}
            onChange={(event) => setPhrase(event.target.value)}
            placeholder="Confirmation phrase"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        {!readiness?.ready ? (
          <div className="rounded-lg border border-down/20 bg-down/[0.06] px-3 py-2.5 text-[11.5px] text-slate-400">
            <span className="flex items-center gap-1.5 font-semibold text-down">
              <IconZap size={11} />
              Live gate not satisfied
            </span>
            <ul className="mt-1.5 list-disc space-y-1 pl-4">
              {(readiness?.reasons.length ? readiness.reasons : ["Live readiness unavailable."]).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {error ? (
          <div className="rounded-lg border border-down/30 bg-down/10 px-3 py-2 text-[11.5px] text-down">{error}</div>
        ) : null}
      </div>
    </Modal>
  );
}

export function KillSwitchModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { killSwitch } = useBot();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) setBusy(false);
  }, [open]);

  const confirm = async () => {
    setBusy(true);
    const result = await killSwitch(true);
    setBusy(false);
    if (result.ok) onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Emergency control"
      title="Engage the kill switch?"
      subtitle="This stops the bot immediately and blocks new orders until the switch is cleared."
      danger
      footer={
        <>
          <button type="button" onClick={onClose} className="btn">
            Cancel
          </button>
          <button type="button" onClick={() => void confirm()} disabled={busy} className="btn btn-danger">
            {busy ? "Engaging…" : "Engage kill switch"}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <Warning>
          The kill switch is a hard halt: the engine stops, no new orders are submitted, and a risk halt reason is
          persisted. Paper and live positions remain in the database for reconciliation.
        </Warning>
        <div className="text-[11.5px] leading-relaxed text-slate-500">
          Clear the switch from the same header control when you are ready to resume. Starting the bot while the
          switch is active is rejected by the backend.
        </div>
      </div>
    </Modal>
  );
}
