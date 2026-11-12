import type { BookSnapshot, Side } from "../api/types";
import { cx, fmtCents, fmtNum, isNum } from "../lib/format";
import { SideBadge } from "./Badges";
import { IconArrowDown, IconArrowUp, IconZap } from "./Icons";
import { Sparkline } from "./Sparkline";

interface PriceCardProps {
  side: Side;
  book: BookSnapshot | null;
  armed: boolean;
  threshold: number | null;
  history: number[];
  inWindow: boolean;
  staleAfterMs: number | null;
  className?: string;
}

export function PriceCard({
  side,
  book,
  armed,
  threshold,
  history,
  inWindow,
  staleAfterMs,
  className,
}: PriceCardProps) {
  const up = side === "UP";
  const ask = book?.best_ask ?? null;
  const bid = book?.best_bid ?? null;
  const spread = isNum(ask) && isNum(bid) ? ask - bid : null;
  const distance = isNum(ask) && isNum(threshold) ? threshold - ask : null;
  const stale = isNum(book?.age_ms) && isNum(staleAfterMs) ? book.age_ms > staleAfterMs : false;

  return (
    <section
      className={cx(
        "panel panel-hover relative overflow-hidden p-5",
        armed && (up ? "glow-up" : "glow-down"),
        className,
      )}
    >
      <div
        className={cx(
          "absolute inset-x-0 top-0 h-px",
          up ? "bg-gradient-to-r from-transparent via-up/70 to-transparent" : "bg-gradient-to-r from-transparent via-down/70 to-transparent",
        )}
      />
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <SideBadge side={side} />
          <span className="label">Best ask</span>
        </div>
        {armed ? (
          <span className={cx("chip", up ? "border-up/35 bg-up/10 text-up" : "border-down/35 bg-down/10 text-down")}>
            <IconZap size={10} />
            Armed
          </span>
        ) : (
          <span className="chip border-white/10 bg-white/5 text-slate-400">
            {inWindow ? "Monitoring" : "Standby"}
          </span>
        )}
      </div>

      <div className="mt-4 flex items-end justify-between gap-4">
        <div>
          <div className={cx("mono text-[34px] font-semibold leading-none tracking-tight", up ? "text-up" : "text-down")}>
            {fmtCents(ask)}
          </div>
          <div className="mono mt-1.5 text-[12px] text-muted">
            {isNum(ask) ? `$${ask.toFixed(3)}` : "no quote"}
          </div>
        </div>
        <div className="w-[42%]">
          <Sparkline data={history} tone={up ? "up" : "down"} height={40} />
        </div>
      </div>

      <div className="mt-4">
        <div className="relative h-1.5 overflow-hidden rounded-full bg-white/6">
          <div
            className={cx("h-full rounded-full", up ? "bg-up" : "bg-down")}
            style={{ width: `${Math.min(100, Math.max(0, (ask ?? 0) * 100))}%` }}
          />
          {isNum(threshold) ? (
            <div
              className="absolute top-0 h-full w-px bg-white/55"
              style={{ left: `${threshold * 100}%` }}
              title={`Trigger ${fmtCents(threshold)}`}
            />
          ) : null}
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px] text-slate-600">
          <span className="mono">0¢</span>
          <span className="mono">trigger {fmtCents(threshold)}</span>
          <span className="mono">100¢</span>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-1.5 text-[11.5px]">
        {armed ? (
          <span className={up ? "text-up" : "text-down"}>
            Ask is at or above the trigger — marketable BUY condition met.
          </span>
        ) : isNum(distance) && distance > 0 ? (
          <span className="text-muted">
            <span className={cx("mono", up ? "text-up" : "text-down")}>{fmtCents(distance)}</span> below trigger
          </span>
        ) : (
          <span className="text-slate-600">Waiting for a fresh quote</span>
        )}
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2 border-t hairline pt-3.5">
        <div>
          <div className="label !text-[9px]">Bid</div>
          <div className="mono mt-1 text-[12.5px] text-slate-300">{fmtCents(bid)}</div>
        </div>
        <div>
          <div className="label !text-[9px]">Spread</div>
          <div className="mono mt-1 text-[12.5px] text-slate-300">{fmtCents(spread)}</div>
        </div>
        <div>
          <div className="label !text-[9px]">Ask size</div>
          <div className="mono mt-1 text-[12.5px] text-slate-300">
            {book && isNum(book.ask_size) ? `${fmtNum(book.ask_size, 0)} sh` : "—"}
          </div>
        </div>
      </div>
      <div className="mt-2.5 flex items-center justify-between text-[10px]">
        <span className={stale ? "text-amber" : "text-slate-600"}>
          {book ? (stale ? "stale quote" : "fresh quote") : "no book"}
        </span>
        <span className="text-slate-600">{book ? book.source : "waiting"}</span>
      </div>
      {armed ? (
        <div className="pointer-events-none absolute -right-7 -top-7 h-20 w-20 rounded-full bg-gradient-to-br from-white/8 to-transparent blur-xl" />
      ) : up ? (
        <IconArrowUp size={110} className="pointer-events-none absolute -bottom-6 -right-5 text-up/[0.04]" />
      ) : (
        <IconArrowDown size={110} className="pointer-events-none absolute -bottom-6 -right-5 text-down/[0.04]" />
      )}
    </section>
  );
}
