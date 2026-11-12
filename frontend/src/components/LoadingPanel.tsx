import { cx } from "../lib/format";

export function LoadingPanel({ className }: { className?: string }) {
  return (
    <section className={cx("panel p-5", className)}>
      <div className="label">Waiting for the backend feed</div>
    </section>
  );
}
