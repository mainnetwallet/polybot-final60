import { useEffect, useState } from "react";
import { useBot } from "./BotContext";

/** Seconds until the active market expires, derived from its real expiry timestamp. */
export function useRemainingSeconds(): number | null {
  const { snapshot, clockOffsetMs } = useBot();
  const end = snapshot?.market ? Date.parse(snapshot.market.window_end) : NaN;
  const active = Number.isFinite(end);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 50);
    return () => window.clearInterval(timer);
  }, [active, end]);

  return active ? (end - (now + clockOffsetMs)) / 1000 : null;
}
