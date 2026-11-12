import type { ReactNode } from "react";

export function EmptyState({
  title,
  hint,
  icon,
}: {
  title: string;
  hint?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      {icon ? <div className="mb-3 text-slate-700">{icon}</div> : null}
      <div className="text-[13px] font-medium text-slate-400">{title}</div>
      {hint ? <div className="mt-1 max-w-sm text-[11.5px] leading-relaxed text-slate-600">{hint}</div> : null}
    </div>
  );
}
