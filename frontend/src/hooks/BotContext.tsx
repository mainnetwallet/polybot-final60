import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api/client";
import type { LogRow, Mode, Snapshot } from "../api/types";

export type ActionResult = { ok: boolean; error?: string };
export type ToastKind = "success" | "error" | "info" | "warning";
export type FeedState = "connecting" | "open" | "offline";

export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  message?: string;
}

interface PriceHistory {
  up: number[];
  down: number[];
}

interface BotContextValue {
  snapshot: Snapshot | null;
  feedState: FeedState;
  logs: LogRow[];
  history: PriceHistory;
  toasts: Toast[];
  refresh: () => Promise<void>;
  start: (phrase?: string) => Promise<ActionResult>;
  stop: () => Promise<ActionResult>;
  switchMode: (mode: Mode, phrase?: string) => Promise<ActionResult>;
  killSwitch: (active: boolean) => Promise<ActionResult>;
  resetPaper: (balance: number) => Promise<ActionResult>;
  notify: (kind: ToastKind, title: string, message?: string) => void;
  dismissToast: (id: number) => void;
  clearLogs: () => void;
}

const BotContext = createContext<BotContextValue | null>(null);

function describeError(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

const HISTORY_LIMIT = 180;
const LOG_LIMIT = 400;

function pushPoint(points: number[], value: number | null | undefined): number[] {
  if (typeof value !== "number" || !Number.isFinite(value)) return points;
  const next = [...points, value];
  return next.length > HISTORY_LIMIT ? next.slice(next.length - HISTORY_LIMIT) : next;
}

export function BotProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [feedState, setFeedState] = useState<FeedState>("connecting");
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [history, setHistory] = useState<PriceHistory>({ up: [], down: [] });
  const [toasts, setToasts] = useState<Toast[]>([]);

  const snapshotRef = useRef<Snapshot | null>(null);
  const toastSeq = useRef(0);
  const toastTimers = useRef<Set<number>>(new Set());

  const applySnapshot = useCallback((next: Snapshot) => {
    snapshotRef.current = next;
    setSnapshot(next);
    setHistory((prev) => ({
      up: pushPoint(prev.up, next.books.up?.best_ask),
      down: pushPoint(prev.down, next.books.down?.best_ask),
    }));
  }, []);

  const appendLog = useCallback((entry: LogRow) => {
    setLogs((prev) => {
      const next = [entry, ...prev];
      return next.length > LOG_LIMIT ? next.slice(0, LOG_LIMIT) : next;
    });
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const notify = useCallback(
    (kind: ToastKind, title: string, message?: string) => {
      const id = ++toastSeq.current;
      setToasts((prev) => [...prev.slice(-3), { id, kind, title, message }]);
      const timer = window.setTimeout(() => {
        toastTimers.current.delete(timer);
        dismissToast(id);
      }, kind === "error" ? 8000 : 5000);
      toastTimers.current.add(timer);
    },
    [dismissToast],
  );

  useEffect(() => {
    const timers = toastTimers.current;
    return () => {
      timers.forEach((timer) => window.clearTimeout(timer));
      timers.clear();
    };
  }, []);

  useEffect(() => {
    let alive = true;
    api
      .state()
      .then((next) => {
        if (alive) applySnapshot(next);
      })
      .catch(() => undefined);
    api
      .logs(200)
      .then((rows) => {
        if (alive) setLogs(rows);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [applySnapshot]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let disposed = false;
    let retry: number | undefined;

    const connect = () => {
      if (disposed) return;
      setFeedState("connecting");
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${window.location.host}/api/ws`);

      socket.onopen = () => {
        if (!disposed) setFeedState("open");
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as { type?: string; data?: unknown };
          if (message.type === "state" && message.data) {
            applySnapshot(message.data as Snapshot);
          } else if (message.type === "log" && message.data) {
            const row = message.data as Omit<LogRow, "created_at">;
            appendLog({ ...row, created_at: new Date().toISOString() });
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onerror = () => {
        socket?.close();
      };
      socket.onclose = () => {
        if (disposed) return;
        setFeedState("offline");
        retry = window.setTimeout(connect, 1500);
      };
    };

    connect();
    const ping = window.setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send("ping");
    }, 20_000);

    return () => {
      disposed = true;
      window.clearInterval(ping);
      if (retry !== undefined) window.clearTimeout(retry);
      socket?.close();
    };
  }, [appendLog, applySnapshot]);

  useEffect(() => {
    if (feedState === "open") return;
    let alive = true;
    const tick = () => {
      api
        .state()
        .then((next) => {
          if (alive) applySnapshot(next);
        })
        .catch(() => undefined);
    };
    tick();
    const timer = window.setInterval(tick, 2500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [applySnapshot, feedState]);

  const refresh = useCallback(async () => {
    try {
      applySnapshot(await api.state());
    } catch {
      /* the feed indicator already surfaces connectivity */
    }
  }, [applySnapshot]);

  const start = useCallback(
    async (phrase = ""): Promise<ActionResult> => {
      const live = snapshotRef.current?.bot.mode === "live";
      try {
        applySnapshot(await api.start(live, phrase));
        notify(
          "success",
          live ? "LIVE engine started" : "Paper engine started",
          live ? "Real orders can be submitted from this session." : "Simulating fills with live Polymarket data.",
        );
        return { ok: true };
      } catch (error) {
        const message = describeError(error);
        notify("error", "Could not start the engine", message);
        return { ok: false, error: message };
      }
    },
    [applySnapshot, notify],
  );

  const stop = useCallback(async (): Promise<ActionResult> => {
    try {
      applySnapshot(await api.stop());
      notify("info", "Engine stopped", "No new orders will be submitted.");
      return { ok: true };
    } catch (error) {
      const message = describeError(error);
      notify("error", "Could not stop the engine", message);
      return { ok: false, error: message };
    }
  }, [applySnapshot, notify]);

  const switchMode = useCallback(
    async (mode: Mode, phrase = ""): Promise<ActionResult> => {
      try {
        applySnapshot(await api.switchMode(mode, mode === "live", phrase));
        notify(
          mode === "live" ? "warning" : "info",
          mode === "live" ? "Execution mode: LIVE" : "Execution mode: PAPER",
          mode === "live"
            ? "The live engine will send real orders when started."
            : "Back to simulated execution on live data.",
        );
        return { ok: true };
      } catch (error) {
        const message = describeError(error);
        notify("error", "Mode switch rejected", message);
        return { ok: false, error: message };
      }
    },
    [applySnapshot, notify],
  );

  const killSwitch = useCallback(
    async (active: boolean): Promise<ActionResult> => {
      try {
        applySnapshot(await api.killSwitch(active));
        notify(
          active ? "warning" : "info",
          active ? "Kill switch engaged" : "Kill switch cleared",
          active ? "Bot stopped. Clear the switch before restarting." : "The bot can be started again.",
        );
        return { ok: true };
      } catch (error) {
        const message = describeError(error);
        notify("error", "Kill switch request failed", message);
        return { ok: false, error: message };
      }
    },
    [applySnapshot, notify],
  );

  const resetPaper = useCallback(
    async (balance: number): Promise<ActionResult> => {
      try {
        applySnapshot(await api.resetPaper(balance));
        setHistory({ up: [], down: [] });
        notify("success", "Paper account reset", `Starting balance set to $${balance.toFixed(2)}.`);
        return { ok: true };
      } catch (error) {
        const message = describeError(error);
        notify("error", "Reset failed", message);
        return { ok: false, error: message };
      }
    },
    [applySnapshot, notify],
  );

  const clearLogs = useCallback(() => setLogs([]), []);

  const value = useMemo<BotContextValue>(
    () => ({
      snapshot,
      feedState,
      logs,
      history,
      toasts,
      refresh,
      start,
      stop,
      switchMode,
      killSwitch,
      resetPaper,
      notify,
      dismissToast,
      clearLogs,
    }),
    [
      snapshot,
      feedState,
      logs,
      history,
      toasts,
      refresh,
      start,
      stop,
      switchMode,
      killSwitch,
      resetPaper,
      notify,
      dismissToast,
      clearLogs,
    ],
  );

  return <BotContext.Provider value={value}>{children}</BotContext.Provider>;
}

export function useBot(): BotContextValue {
  const context = useContext(BotContext);
  if (!context) throw new Error("useBot must be used inside BotProvider");
  return context;
}
