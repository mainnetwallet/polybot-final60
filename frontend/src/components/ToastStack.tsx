import { useBot, type Toast } from "../hooks/BotContext";
import { cx } from "../lib/format";
import { IconAlert, IconCheck, IconClose, IconZap } from "./Icons";

function toastTone(kind: Toast["kind"]): { border: string; icon: string; bar: string } {
  switch (kind) {
    case "success":
      return { border: "border-up/25", icon: "text-up", bar: "bg-up" };
    case "error":
      return { border: "border-down/30", icon: "text-down", bar: "bg-down" };
    case "warning":
      return { border: "border-amber/30", icon: "text-amber", bar: "bg-amber" };
    default:
      return { border: "border-sky/25", icon: "text-sky", bar: "bg-sky" };
  }
}

export function ToastStack() {
  const { toasts, dismissToast } = useBot();
  if (!toasts.length) return null;

  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[340px] max-w-[calc(100vw-2.5rem)] flex-col gap-2">
      {toasts.map((toast) => {
        const tone = toastTone(toast.kind);
        return (
          <div
            key={toast.id}
            className={cx(
              "pointer-events-auto relative flex gap-3 overflow-hidden rounded-xl border bg-ink-900/95 p-3.5 shadow-[0_24px_60px_-20px_rgba(0,0,0,0.9)] backdrop-blur",
              tone.border,
            )}
          >
            <div className={cx("mt-0.5 shrink-0", tone.icon)}>
              {toast.kind === "success" ? <IconCheck size={15} /> : toast.kind === "error" ? <IconAlert size={15} /> : <IconZap size={15} />}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-[12.5px] font-semibold text-slate-100">{toast.title}</div>
              {toast.message ? (
                <div className="mt-0.5 break-words text-[11.5px] leading-relaxed text-slate-400">{toast.message}</div>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => dismissToast(toast.id)}
              className="shrink-0 self-start rounded p-0.5 text-slate-600 hover:text-slate-300"
              aria-label="Dismiss notification"
            >
              <IconClose size={13} />
            </button>
            <div className={cx("absolute bottom-0 left-0 h-0.5 w-full opacity-40", tone.bar)} />
          </div>
        );
      })}
    </div>
  );
}
