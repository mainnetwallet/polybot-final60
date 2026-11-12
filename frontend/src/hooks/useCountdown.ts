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
    let frame = 0;
    const loop = () => {
      setNow(Date.now());
      frame = window.requestAnimationFrame(loop);
    };
    loop();
    return () => window.cancelAnimationFrame(frame);
  }, [active, end]);

  return active ? (end - (now + clockOffsetMs)) / 1000 : null;
}
