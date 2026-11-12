import { useEffect, type ReactNode } from "react";
import { cx } from "../lib/format";
import { IconClose } from "./Icons";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  eyebrow?: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
  maxWidth?: string;
  danger?: boolean;
}

export function Modal({
  open,
  onClose,
  title,
  eyebrow,
  subtitle,
  children,
  footer,
  maxWidth = "max-w-lg",
  danger = false,
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close dialog"
        className="absolute inset-0 cursor-default bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        className={cx(
          "panel rise relative w-full overflow-hidden",
          maxWidth,
          danger && "border-down/30",
        )}
      >
        <div className="flex items-start justify-between gap-6 border-b hairline px-6 pb-4 pt-5">
          <div>
            {eyebrow ? <div className="label mb-2">{eyebrow}</div> : null}
            <h2 className="text-lg font-semibold tracking-tight text-slate-100">{title}</h2>
            {subtitle ? <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{subtitle}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost -mr-2 -mt-1 h-8 w-8 rounded-lg p-0"
            aria-label="Close"
          >
            <IconClose size={16} />
          </button>
        </div>
        <div className="px-6 py-5">{children}</div>
        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t hairline bg-black/20 px-6 py-4">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
}
